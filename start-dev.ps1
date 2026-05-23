param(
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 4200
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
# Angular frontend folder in this workspace
$frontendDir = Join-Path $root "App-assistante-juridique-ia"

if (-not (Test-Path $frontendDir)) {
    throw "frontend directory not found at: $frontendDir"
}

# If a virtualenv activation script exists, source it before running the API
$activateScript = Join-Path $root ".venv\Scripts\Activate.ps1"
if (Test-Path $activateScript) {
    $apiCmd = "Set-Location '$root'; . '$activateScript'; python src/api.py --port $ApiPort"
}
else {
    $apiCmd = "Set-Location '$root'; python src/api.py --port $ApiPort"
}

# Start Angular dev server and forward the port to ng (npm start -> ng serve)
$frontendCmd = "Set-Location '$frontendDir'; npm run start -- --port $FrontendPort"

$apiProcess = Start-Process powershell -ArgumentList @("-NoExit", "-Command", $apiCmd) -PassThru
$frontendProcess = Start-Process powershell -ArgumentList @("-NoExit", "-Command", $frontendCmd) -PassThru

Write-Host "Started API terminal (PID: $($apiProcess.Id)) on port $ApiPort"
Write-Host "Started frontend terminal (PID: $($frontendProcess.Id)) on port $FrontendPort"
Write-Host "Frontend URL: http://localhost:$FrontendPort"
