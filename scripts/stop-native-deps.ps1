$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$memuraiExe = "C:\Program Files\Memurai\memurai.exe"
$memuraiConfig = Join-Path $projectRoot "docker\memurai.dev.conf"
$listener = Get-NetTCPConnection -LocalPort 6380 -State Listen `
    -ErrorAction SilentlyContinue | Select-Object -First 1

if (-not $listener) {
    Write-Output "Project Memurai is not running."
    exit 0
}

$process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
if (
    $process.ExecutablePath -ne $memuraiExe -or
    $process.CommandLine -notlike "*$memuraiConfig*"
) {
    throw "Refusing to stop port 6380 because it is not the project Memurai instance."
}

Stop-Process -Id $process.ProcessId
Write-Output "Stopped project Memurai PID $($process.ProcessId). PostgreSQL was left running."
