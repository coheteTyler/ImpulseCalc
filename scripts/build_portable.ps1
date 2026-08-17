# Build ImpulseCalc.exe (PyInstaller) for Discord/WhatsApp share.
# Usage:  powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = if (Test-Path ".\.venv\Scripts\python.exe") {
  (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
  (Get-Command python).Source
}

Write-Host "Building portable ImpulseCalc.exe with $py"
& $py -m pip install -q -U pip pyinstaller
& $py -m pip install -q -r requirements.txt
& $py -m PyInstaller --noconfirm --clean ImpulseCalc.spec

$exe = Join-Path $Root "dist\ImpulseCalc.exe"
if (-not (Test-Path $exe)) {
  # onedir layout
  $exe = Join-Path $Root "dist\ImpulseCalc\ImpulseCalc.exe"
}
if (Test-Path $exe) {
  Write-Host "OK  $exe" -ForegroundColor Green
} else {
  Write-Host "Build finished but exe not found under dist\" -ForegroundColor Yellow
  exit 1
}
