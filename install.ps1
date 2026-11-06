# Create a desktop shortcut for PromptPolish.
# Usage:  powershell -ExecutionPolicy Bypass -File .\install.ps1
#         powershell -ExecutionPolicy Bypass -File .\install.ps1 -Startup
param(
    [switch]$Startup
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$entry = Join-Path $root 'prompt_polish.pyw'
if (-not (Test-Path $entry)) { throw "prompt_polish.pyw not found next to install.ps1" }

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $candidate = Join-Path (Split-Path -Parent (Get-Command python.exe).Source) 'pythonw.exe'
    if (Test-Path $candidate) { $pythonw = $candidate }
}
if (-not $pythonw) { throw "pythonw.exe not found. Install Python 3.10+ and add it to PATH." }

& $pythonw -c "import tkinter, sys; print(sys.version)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "This Python cannot import tkinter. Reinstall Python with Tcl/Tk support." }

$bridge = Join-Path $root 'prompt_polish\uia_bridge.ps1'
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($bridge, [ref]$null, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) { throw "uia_bridge.ps1 failed to parse: $($errors[0].Message)" }

$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'PromptPolish.lnk'
$link = $shell.CreateShortcut($linkPath)
$link.TargetPath = $pythonw
$link.Arguments = '"' + $entry + '"'
$link.WorkingDirectory = $root
$link.Description = 'DeepSeek Harness prompt optimizer'
$link.IconLocation = "$pythonw,0"
$link.Save()
Write-Host "Desktop shortcut created: $linkPath"

if ($Startup) {
    $startupDir = [Environment]::GetFolderPath('Startup')
    $startupLink = Join-Path $startupDir 'PromptPolish.lnk'
    $s = $shell.CreateShortcut($startupLink)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + $entry + '"'
    $s.WorkingDirectory = $root
    $s.Description = 'DeepSeek Harness prompt optimizer'
    $s.IconLocation = "$pythonw,0"
    $s.Save()
    Write-Host "Startup shortcut created: $startupLink"
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Start PromptPolish from the desktop shortcut."
Write-Host "2. Set a DeepSeek API key (never paste it into chat):"
Write-Host "     setx DEEPSEEK_API_KEY `"sk-...`""
Write-Host "   then start the tool again, OR use Settings -> Write API key."
Write-Host "3. Click 'Calibrate input box' once and click inside the DeepSeek composer."
Write-Host "4. Write a draft, then press Ctrl+Alt+P (or click Optimize)."
