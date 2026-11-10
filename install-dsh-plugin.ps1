# Install the PromptPolish DSH plugin into the desktop profile.
# Usage: powershell -ExecutionPolicy Bypass -File .\install-dsh-plugin.ps1
param(
    [string]$Profile = 'desktop'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root 'dsh-plugin-prompt-polish'
if (-not (Test-Path $src)) { throw "Plugin folder not found: $src" }

$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
$profileDir = Join-Path $dshHome "profiles\$Profile"
if (-not (Test-Path $profileDir)) { throw "DSH profile not found: $profileDir" }

$dest = Join-Path $profileDir 'node_modules\dsh-plugin-prompt-polish'
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
Copy-Item $src $dest -Recurse -Force
Write-Host "Copied plugin to: $dest"

$patchPath = Join-Path $profileDir 'cordis.patch.yml'
if (-not (Test-Path $patchPath)) { throw "Missing profile patch: $patchPath" }
$backup = "$patchPath.bak-prompt-polish"
if (-not (Test-Path $backup)) { Copy-Item $patchPath $backup -Force }
$patch = Get-Content $patchPath -Raw -Encoding utf8
if ($patch -notmatch 'dsh-plugin-prompt-polish') {
    $patch = $patch.TrimEnd() + "`n`n- insert:`n    - id: prompt-polish`n      name: dsh-plugin-prompt-polish`n"
    Set-Content $patchPath -Value $patch -Encoding utf8 -NoNewline
    Write-Host "Added loader entry to: $patchPath"
} else {
    Write-Host "Loader entry already present."
}

# Best-effort import check. The host itself loads this package on restart.
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) {
    Push-Location $profileDir
    try {
        & $node.Source --input-type=module -e "import('dsh-plugin-prompt-polish').then(()=>process.exit(0)).catch(e=>{console.error(e.message);process.exit(1)})"
        if ($LASTEXITCODE -ne 0) { throw "Plugin import check failed (see message above)." }
        Write-Host "Host plugin import check passed."
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "Done. Restart DeepSeek Harness once to load the host route and client bundle."
Write-Host "After restart, a small sparkle icon appears immediately left of the model selector."
Write-Host "If it does not appear, open the browser devtools console and look for prompt-polish errors."
