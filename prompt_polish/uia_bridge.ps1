# PromptPolish UI Automation bridge for the DeepSeek Harness composer.
#
# The script is deliberately ASCII-only: Windows PowerShell 5.1 parses scripts
# without a BOM as ANSI, so localized text is supplied by the Python side and
# all diagnostics are stable codes. Only the composer value is read; chat
# messages are never enumerated, and Enter / the send button are never pressed.
#
# Actions: read | apply | status
# Output: one compact JSON object on stdout.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('read', 'apply', 'status')]
    [string]$Action,
    [long]$Hwnd = 0,
    [double]$RelX = 0.53,
    [double]$RelY = 0.78,
    [string]$ComposerPrefix = '',
    [string]$ExpectedTitle = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$inputPayload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$ExpectedDraft = [string]$inputPayload.expected_draft
$NewText = [string]$inputPayload.new_text

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class PP {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr p);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public static string Title(IntPtr h) { var sb = new StringBuilder(512); GetWindowText(h, sb, 512); return sb.ToString(); }
    public static void Click(int x, int y) {
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(120);
        mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(70);
        mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);
    }
    public static void Key(byte vk) {
        keybd_event(vk, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(28);
        keybd_event(vk, 0, 2, UIntPtr.Zero);
        System.Threading.Thread.Sleep(45);
    }
    public static void Ctrl(byte vk) {
        keybd_event(0x11, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(25);
        Key(vk);
        keybd_event(0x11, 0, 2, UIntPtr.Zero);
        System.Threading.Thread.Sleep(120);
    }
    public static bool Foreground(IntPtr h) {
        if (SetForegroundWindow(h)) return true;
        IntPtr fg = GetForegroundWindow();
        uint ft = GetWindowThreadProcessId(fg, IntPtr.Zero);
        uint mt = GetCurrentThreadId();
        bool attached = false;
        try {
            if (ft != 0 && ft != mt) attached = AttachThreadInput(mt, ft, true);
            return SetForegroundWindow(h);
        } finally {
            if (attached) AttachThreadInput(mt, ft, false);
        }
    }
}
"@

[PP]::SetProcessDPIAware() | Out-Null

function Write-Result($obj) {
    $obj | ConvertTo-Json -Compress -Depth 6
    exit 0
}

function Fail([string]$code, [string]$message) {
    Write-Result ([ordered]@{ ok = $false; action = $Action; code = $code; message = $message })
}

function Normalize([string]$s) {
    if ($null -eq $s) { return '' }
    $s = $s -replace "`r`n", "`n" -replace "`r", "`n"
    if ($s.Trim().Length -eq 0) { return '' }
    return $s.Trim([char[]]@([char]10))
}

function Get-Title([IntPtr]$h) {
    return [PP]::Title($h)
}

function Get-Rect([IntPtr]$h) {
    $r = New-Object PP+RECT
    [PP]::GetWindowRect($h, [ref]$r) | Out-Null
    return $r
}

function Get-RuntimeKey($el) {
    try { return ($el.GetRuntimeId() -join ',') } catch { return '' }
}

function Test-Focused($composer) {
    try {
        $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
        if ($null -eq $focused) { return $false }
        if ((Get-RuntimeKey $focused) -eq (Get-RuntimeKey $composer)) { return $true }
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $node = $focused
        for ($i = 0; $i -lt 4; $i++) {
            $node = $walker.GetParent($node)
            if ($null -eq $node) { break }
            if ((Get-RuntimeKey $node) -eq (Get-RuntimeKey $composer)) { return $true }
        }
        if ($focused.Current.Name -eq $composer.Current.Name -and $focused.Current.ClassName -like '*input*') {
            return $true
        }
        return $false
    } catch { return $false }
}

function Get-Value($composer) {
    $vp = $null
    if ($composer.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
        return Normalize $vp.Current.Value
    }
    $tp = $null
    if ($composer.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$tp)) {
        return Normalize $tp.DocumentRange.GetText(-1)
    }
    return $null
}

function Get-Composer($win) {
    $editCond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Edit)
    $edits = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)
    foreach ($e in $edits) {
        try {
            if ($e.Current.IsOffscreen) { continue }
            if ($ComposerPrefix -ne '' -and $e.Current.Name -like ($ComposerPrefix + '*')) { return $e }
        } catch { }
    }
    # Locale-independent fallback: the composer card contains the add button
    # (class ending in _add) and exactly one visible edit.
    try {
        $btnCond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Button)
        $buttons = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $btnCond)
        foreach ($btn in $buttons) {
            try {
                if ($btn.Current.ClassName -notlike '*_add') { continue }
            } catch { continue }
            $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
            $card = $btn
            for ($i = 0; $i -lt 4; $i++) {
                $card = $walker.GetParent($card)
                if ($null -eq $card) { break }
                if ($card.Current.ClassName -like '*_card*') { break }
            }
            if ($null -eq $card) { continue }
            $inner = $card.FindAll([System.Windows.Automation.TreeScope]::Descendants, $editCond)
            foreach ($e in $inner) {
                try { if (-not $e.Current.IsOffscreen) { return $e } } catch { }
            }
        }
    } catch { }
    return $null
}

function Get-Special($composer) {
    $reasons = New-Object System.Collections.Generic.List[string]
    try {
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $card = $walker.GetParent($composer)
        if ($null -ne $card) {
            $desc = $card.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($e in $desc) {
                try {
                    $c = $e.Current.ClassName
                    $t = $e.Current.ControlType.ProgrammaticName
                    if ($t -eq 'ControlType.Image') {
                        $reasons.Add('attachment_image') | Out-Null
                    }
                    elseif ($t -eq 'ControlType.List') {
                        $reasons.Add('mention_popup') | Out-Null
                    }
                    elseif ($t -eq 'ControlType.Button' -and $c -match 'remove') {
                        $reasons.Add('attachment_remove_button') | Out-Null
                    }
                    elseif ($c -match 'mention|reference|chip|attachment|thumbnail|rail') {
                        $reasons.Add('reference_or_attachment_node') | Out-Null
                    }
                } catch { }
            }
        }
    } catch { }
    return @($reasons | Select-Object -Unique)
}

function Click-Composer([IntPtr]$h) {
    $r = Get-Rect $h
    $w = $r.Right - $r.Left
    $ht = $r.Bottom - $r.Top
    if ($w -le 0 -or $ht -le 0) { return $false }
    $x = [int]($r.Left + $w * $RelX)
    $y = [int]($r.Top + $ht * $RelY)
    [PP]::Foreground($h) | Out-Null
    Start-Sleep -Milliseconds 220
    if ([PP]::GetForegroundWindow() -ne $h) {
        $script:clickError = 'foreground_failed'
        return $false
    }
    [PP]::Click($x, $y)
    return $true
}

function Find-ComposerAfterClick([IntPtr]$h) {
    if ([PP]::IsIconic($h)) { [PP]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 300 }
    if (-not (Click-Composer $h)) { return $null }
    $win = [System.Windows.Automation.AutomationElement]::FromHandle($h)
    $composer = $null
    for ($i = 0; $i -lt 14; $i++) {
        Start-Sleep -Milliseconds 250
        try { $composer = Get-Composer $win } catch { $composer = $null }
        if ($null -ne $composer) { break }
    }
    if ($null -eq $composer) { return $null }
    if (-not (Test-Focused $composer)) {
        try { $composer.SetFocus() } catch { }
        Start-Sleep -Milliseconds 250
    }
    return $composer
}

function Set-Clipboard-Retry([string]$text) {
    for ($i = 0; $i -lt 4; $i++) {
        try { Set-Clipboard -Value $text -ErrorAction Stop; return $true } catch { Start-Sleep -Milliseconds 120 }
    }
    return $false
}

try {
    if ($Hwnd -eq 0 -or -not [PP]::IsWindow([IntPtr]$Hwnd)) {
        Fail 'window_gone' 'target window is gone'
    }
    $h = [IntPtr]$Hwnd
    $title = Get-Title $h

    if ($Action -eq 'status') {
        Write-Result ([ordered]@{ ok = $true; action = 'status'; title = $title; hwnd = $Hwnd })
    }

    if ($ExpectedTitle -ne '' -and $title -ne $ExpectedTitle) {
        Write-Result ([ordered]@{
            ok = $false; action = $Action; code = 'chat_changed'
            message = 'active chat title no longer matches'; title = $title
        })
    }

    $script:clickError = ''
    $composer = Find-ComposerAfterClick $h
    if ($null -eq $composer) {
        if ($script:clickError -eq 'foreground_failed') {
            Fail 'foreground_failed' 'could not bring the target window to the foreground'
        }
        Fail 'composer_not_found' 'could not locate the composer edit control'
    }

    $titleAfterClick = Get-Title $h
    if ($ExpectedTitle -ne '' -and $titleAfterClick -ne $ExpectedTitle) {
        Write-Result ([ordered]@{
            ok = $false; action = $Action; code = 'chat_changed'
            message = 'chat changed during activation'; title = $titleAfterClick
        })
    }
    $title = $titleAfterClick

    if (-not (Test-Focused $composer)) {
        Fail 'focus_failed' 'focus is not in the composer; refusing to read or write'
    }

    $current = Get-Value $composer
    if ($null -eq $current) {
        Fail 'unsupported' 'composer does not expose a readable value'
    }
    $special = @(Get-Special $composer)

    if ($Action -eq 'read') {
        Write-Result ([ordered]@{
            ok = $true; action = 'read'; value = $current; special = $special
            title = $title; hwnd = $Hwnd; length = $current.Length
        })
    }

    # ---- apply ----
    if ($special.Count -gt 0) {
        Write-Result ([ordered]@{
            ok = $false; action = 'apply'; code = 'special'
            message = 'composer has attachments or special references'; special = $special
        })
    }
    if ($current -cne $ExpectedDraft) {
        Write-Result ([ordered]@{
            ok = $false; action = 'apply'; code = 'draft_changed'
            message = 'draft changed while optimizing'; length = $current.Length
        })
    }
    $expected = Normalize $NewText
    if ($expected -eq '') {
        Fail 'empty_result' 'refusing to write an empty result'
    }
    # Re-check immediately before any keystroke: no chat switch, focus intact.
    if ($ExpectedTitle -ne '' -and (Get-Title $h) -ne $ExpectedTitle) {
        Write-Result ([ordered]@{
            ok = $false; action = 'apply'; code = 'chat_changed'
            message = 'chat changed just before write'; title = (Get-Title $h)
        })
    }
    if (-not (Test-Focused $composer)) {
        Fail 'focus_failed' 'focus left the composer before writing'
    }

    # Address the identified control directly; do not use global keystrokes
    # or the clipboard, which can affect another window or overwrite data.
    $vp = $null
    if (-not $composer.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp) -or $vp.Current.IsReadOnly) {
        Fail 'write_unsupported' 'composer does not expose a writable ValuePattern'
    }
    if ([PP]::GetForegroundWindow() -ne $h -or -not (Test-Focused $composer)) {
        Fail 'focus_failed' 'focus left the target before writing'
    }
    if ($ExpectedTitle -ne '' -and (Get-Title $h) -cne $ExpectedTitle) {
        Fail 'chat_changed' 'chat changed before writing'
    }
    if ((Get-Value $composer) -cne (Normalize $ExpectedDraft) -or @(Get-Special $composer).Count -gt 0) {
        Fail 'draft_changed' 'draft changed before writing'
    }
    try {
        $vp.SetValue($NewText)
        Start-Sleep -Milliseconds 250
        if ((Get-Value $composer) -cne $expected) {
            Fail 'write_failed' 'could not verify written value'
        }
    } catch {
        Fail 'write_failed' 'write status unknown'
    }

    Write-Result ([ordered]@{
        ok = $true; action = 'apply'; value = $expected; title = $title
        hwnd = $Hwnd; length = $expected.Length
    })
}
catch {
    $msg = $_.Exception.Message
    if ($msg.Length -gt 300) { $msg = $msg.Substring(0, 300) }
    Fail 'exception' $msg
}
