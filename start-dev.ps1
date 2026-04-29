param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $root "frontend"

if (-not (Test-Path $frontendDir)) {
    throw "frontend directory not found at: $frontendDir"
}

$apiCmd = "Set-Location '$root'; python src/api.py --port $ApiPort"
$frontendCmd = "Set-Location '$frontendDir'; `$env:PORT='$FrontendPort'; npm run dev"

$apiProcess = Start-Process powershell -ArgumentList @("-NoExit", "-Command", $apiCmd) -PassThru
$frontendProcess = Start-Process powershell -ArgumentList @("-NoExit", "-Command", $frontendCmd) -PassThru

Write-Host "Started API terminal (PID: $($apiProcess.Id)) on port $ApiPort"
Write-Host "Started frontend terminal (PID: $($frontendProcess.Id)) on port $FrontendPort"
Write-Host "Frontend URL: http://localhost:$FrontendPort"
