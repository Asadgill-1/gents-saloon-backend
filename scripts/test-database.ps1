$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentFile = Join-Path $projectRoot ".env"
$testDatabase = "gents_saloon_phase1_test"
$postgresBin = "C:\Program Files\PostgreSQL\17\bin"

if ($testDatabase -ne "gents_saloon_phase1_test") {
    throw "Refusing to recreate an unexpected database."
}
if (-not (Test-Path -LiteralPath $environmentFile)) {
    throw "Missing project .env file."
}
if (-not (Test-Path -LiteralPath (Join-Path $postgresBin "psql.exe"))) {
    throw "PostgreSQL 17 client tools are not installed at the expected path."
}

$databaseLine = Get-Content -LiteralPath $environmentFile |
    Where-Object { $_ -match "^DATABASE_URL=" } |
    Select-Object -First 1
if (-not $databaseLine) {
    throw "DATABASE_URL is missing from .env."
}

$databaseUrl = $databaseLine.Substring("DATABASE_URL=".Length)
$pattern = "^postgresql(?:\+asyncpg)?://([^:]+):([^@]+)@([^:/]+):(\d+)/([^?]+)"
if ($databaseUrl -notmatch $pattern) {
    throw "DATABASE_URL has an unsupported format."
}

$postgresUser = [uri]::UnescapeDataString($Matches[1])
$env:PGPASSWORD = [uri]::UnescapeDataString($Matches[2])
$postgresHost = $Matches[3]
$postgresPort = $Matches[4]
$dropDb = Join-Path $postgresBin "dropdb.exe"
$createDb = Join-Path $postgresBin "createdb.exe"
$psql = Join-Path $postgresBin "psql.exe"
$previousDatabaseUrl = [Environment]::GetEnvironmentVariable("DATABASE_URL", "Process")
$previousIntegrationFlag = [Environment]::GetEnvironmentVariable(
    "RUN_DATABASE_INTEGRATION",
    "Process"
)

try {
    & $dropDb -w -h $postgresHost -p $postgresPort -U $postgresUser --if-exists --force $testDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove the isolated Phase 1 test database."
    }

    & $createDb -w -h $postgresHost -p $postgresPort -U $postgresUser $testDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the isolated Phase 1 test database."
    }

    $bootstrap = Join-Path $projectRoot "supabase\tests\bootstrap_local.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $bootstrap
    if ($LASTEXITCODE -ne 0) {
        throw "Supabase compatibility bootstrap failed."
    }

    $migrations = Get-ChildItem -LiteralPath (Join-Path $projectRoot "supabase\migrations") `
        -File -Filter "*.sql" |
        Sort-Object Name
    if (-not $migrations) {
        throw "No migration files were found."
    }

    foreach ($migration in $migrations) {
        Write-Host "Applying $($migration.Name)"
        & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
            -U $postgresUser -d $testDatabase -f $migration.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "Migration failed: $($migration.Name)"
        }
    }

    $tests = Join-Path $projectRoot "supabase\tests\phase1_tenant_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $tests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 1 tenant/RLS tests failed."
    }

    $phase2Tests = Join-Path $projectRoot "supabase\tests\phase2_operations_schema.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2Tests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 operations schema tests failed."
    }

    $phase2BookingTests = Join-Path $projectRoot "supabase\tests\phase2_booking_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2BookingTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 booking RLS tests failed."
    }

    $phase2LegalCashTests = Join-Path $projectRoot "supabase\tests\phase2_legal_cash_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2LegalCashTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 legal/cash RLS tests failed."
    }

    $phase2CheckoutTests = Join-Path $projectRoot "supabase\tests\phase2_checkout_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2CheckoutTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 checkout RLS tests failed."
    }

    $phase2CorrectionTests = Join-Path $projectRoot "supabase\tests\phase2_correction_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2CorrectionTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 correction RLS tests failed."
    }

    $phase2PayoutTests = Join-Path $projectRoot "supabase\tests\phase2_payout_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2PayoutTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 payout RLS tests failed."
    }

    $phase2ReportTests = Join-Path $projectRoot `
        "supabase\tests\phase2_reports_einvoice_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase2ReportTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 2 report/e-invoice RLS tests failed."
    }

    $phase3TelegramTests = Join-Path $projectRoot `
        "supabase\tests\phase3_telegram_rls.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $phase3TelegramTests
    if ($LASTEXITCODE -ne 0) {
        throw "Phase 3 Telegram/AI RLS tests failed."
    }

    $platformOnboardingTests = Join-Path $projectRoot `
        "supabase\tests\platform_onboarding_interfaces.sql"
    & $psql -X -w -v ON_ERROR_STOP=1 -h $postgresHost -p $postgresPort `
        -U $postgresUser -d $testDatabase -f $platformOnboardingTests
    if ($LASTEXITCODE -ne 0) {
        throw "Platform onboarding interface RLS tests failed."
    }

    $integrationDatabaseUrl = $databaseUrl -replace `
        "/[^/?]+(?=(?:\?|$))", "/$testDatabase"
    $env:DATABASE_URL = $integrationDatabaseUrl
    $env:RUN_DATABASE_INTEGRATION = "1"
    Push-Location (Join-Path $projectRoot "backend")
    try {
        & uv run pytest -q `
            tests/test_context_database.py `
            tests/test_tenant_onboarding_database.py `
            tests/test_subscription_lifecycle_database.py `
            tests/test_entitlement_surfaces_database.py `
            tests/test_export_offboarding_database.py `
            tests/test_booking_database.py `
            tests/test_legal_cash_database.py `
            tests/test_checkout_database.py `
            tests/test_correction_database.py `
            tests/test_payout_database.py `
            tests/test_customer_bot_flow_database.py `
            tests/test_reception_bot_flow_database.py
        if ($LASTEXITCODE -ne 0) {
            throw "Application database integration tests failed."
        }
    }
    finally {
        Pop-Location
        if ($null -eq $previousIntegrationFlag) {
            Remove-Item Env:RUN_DATABASE_INTEGRATION -ErrorAction SilentlyContinue
        }
        else {
            $env:RUN_DATABASE_INTEGRATION = $previousIntegrationFlag
        }
        if ($null -eq $previousDatabaseUrl) {
            Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:DATABASE_URL = $previousDatabaseUrl
        }
    }

    Write-Host "Phase 1-3 database reconstruction and RLS tests PASS."
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
