# ImpulseCalc — standalone launcher (Windows)
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
#
# Starts only this calculator. Does not start LPRE-Library or Tap-In.
# Leave this window open while you use the app.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host ""
Write-Host "  ImpulseCalc — standalone turbine calculator" -ForegroundColor Cyan
Write-Host "  Isolated: this window is only port 8765. Other apps stay as they are." -ForegroundColor DarkGray
Write-Host ""

$py = $null
if (Test-Path ".\.venv\Scripts\python.exe") {
  $py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
  Write-Host "  Creating .venv ..." -ForegroundColor DarkGray
  $sysPy = $null
  foreach ($c in @(
    "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue).Source
  )) {
    if ($c -and (Test-Path $c)) { $sysPy = $c; break }
  }
  if (-not $sysPy) {
    Write-Host "Python 3.11+ not found. Install from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "Check 'Add python.exe to PATH', then run this script again." -ForegroundColor Yellow
    exit 1
  }
  & $sysPy -m venv .venv
  $py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
}

Write-Host "  Python: $py"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q -r requirements.txt

$ErrorActionPreference = "SilentlyContinue"
Write-Host "  Freeing port 8765 if needed..."
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
  ForEach-Object {
    Write-Host "    kill PID $($_.OwningProcess)"
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
  }
Get-CimInstance Win32_Process -Filter "name='python.exe' OR name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'ImpulseCalc\\server\.py|impulsecalc_main\.py' } |
  ForEach-Object {
    Write-Host "    kill server PID $($_.ProcessId)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Start-Sleep -Seconds 1
$ErrorActionPreference = "Stop"

$env:PYTHONPATH = $Root
Write-Host ""
Write-Host "  Open:  http://127.0.0.1:8765/calc.html" -ForegroundColor Green
Write-Host "  Keep this window open. Close it to stop the calculator." -ForegroundColor DarkGray
Write-Host ""

& $py server.py
$code = $LASTEXITCODE
if ($code -ne 0) {
  Write-Host ""
  Write-Host "Server exited with code $code" -ForegroundColor Red
  if ($Host.Name -eq "ConsoleHost") { pause }
  exit $code
}
