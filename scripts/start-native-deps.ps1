$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$postgresBin = "C:\Program Files\PostgreSQL\17\bin"
$postgresData = "C:\Program Files\PostgreSQL\17\data"
$memuraiExe = "C:\Program Files\Memurai\memurai.exe"
$memuraiCli = "C:\Program Files\Memurai\memurai-cli.exe"
$memuraiConfig = Join-Path $projectRoot "docker\memurai.dev.conf"

foreach ($path in @(
    (Join-Path $postgresBin "pg_ctl.exe"),
    (Join-Path $postgresBin "pg_isready.exe"),
    (Join-Path $postgresBin "psql.exe"),
    (Join-Path $postgresBin "createdb.exe"),
    $postgresData,
    $memuraiExe,
    $memuraiCli,
    $memuraiConfig
)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required native dependency path is missing: $path"
    }
}

$pgIsReady = Join-Path $postgresBin "pg_isready.exe"
& $pgIsReady -h 127.0.0.1 -p 5432 -U postgres *> $null
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $postgresBin "pg_ctl.exe") start -D $postgresData -w
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL failed to start."
    }
}

$env:PGPASSWORD = "local-dev-password"
try {
    $databaseExists = & (Join-Path $postgresBin "psql.exe") `
        -h 127.0.0.1 -p 5432 -U postgres -d postgres -tAc `
        "SELECT 1 FROM pg_database WHERE datname = 'gents_saloon'"
    if ($databaseExists.Trim() -ne "1") {
        & (Join-Path $postgresBin "createdb.exe") `
            -h 127.0.0.1 -p 5432 -U postgres gents_saloon
        if ($LASTEXITCODE -ne 0) {
            throw "The gents_saloon database could not be created."
        }
    }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

$redisListener = Get-NetTCPConnection -LocalPort 6380 -State Listen `
    -ErrorAction SilentlyContinue | Select-Object -First 1
if ($redisListener) {
    $redisProcess = Get-CimInstance Win32_Process `
        -Filter "ProcessId=$($redisListener.OwningProcess)"
    if (
        $redisProcess.ExecutablePath -ne $memuraiExe -or
        $redisProcess.CommandLine -notlike "*$memuraiConfig*"
    ) {
        throw "Port 6380 is occupied by a process other than the project Memurai instance."
    }
}
else {
    Start-Process -FilePath $memuraiExe `
        -ArgumentList "`"$memuraiConfig`"" `
        -WindowStyle Hidden

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        $redisListener = Get-NetTCPConnection -LocalPort 6380 -State Listen `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($redisListener) {
            break
        }
    }
    if (-not $redisListener) {
        throw "Project Memurai failed to start on 127.0.0.1:6380."
    }
}

$env:REDISCLI_AUTH = "local-dev-password"
try {
    $redisPing = & $memuraiCli -h 127.0.0.1 -p 6380 ping
}
finally {
    Remove-Item Env:REDISCLI_AUTH -ErrorAction SilentlyContinue
}
if ($redisPing.Trim() -ne "PONG") {
    throw "Authenticated Memurai health check failed."
}

Write-Output "PostgreSQL ready on 127.0.0.1:5432 (database: gents_saloon)"
Write-Output "Memurai ready on 127.0.0.1:6380 (authentication required)"
