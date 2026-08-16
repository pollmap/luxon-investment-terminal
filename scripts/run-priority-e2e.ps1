param(
    [string]$Markets = "KR",
    [string]$Years = "2020:2025",
    [string]$Policy = "street_comparable",
    [switch]$Persist,
    [switch]$ForceRefresh,
    [switch]$ContinueOnError,
    [switch]$RequireConsensusForecast,
    [switch]$RequireBlob,
    [switch]$Strict,
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$DoctorMarkets = $Markets
if ($DoctorMarkets.Trim().ToUpperInvariant() -eq "ALL") {
    $DoctorMarkets = "KR,US,JP"
}

$doctorArgs = @(
    "-m", "services.ingestion_worker.cli",
    "doctor",
    "--markets", $DoctorMarkets
)
if ($RequireBlob) {
    $doctorArgs += "--require-blob"
}
if ($Strict) {
    $doctorArgs += "--strict"
}

Write-Host "== LUXON priority E2E doctor ==" -ForegroundColor Cyan
& python @doctorArgs

$runArgs = @(
    "-m", "services.ingestion_worker.cli",
    "run-priority-e2e",
    "--markets", $Markets,
    "--years", $Years,
    "--policy", $Policy
)
if ($Persist) {
    $runArgs += "--persist"
}
if ($ForceRefresh) {
    $runArgs += "--force-refresh"
}
if ($ContinueOnError) {
    $runArgs += "--continue-on-error"
}
if ($RequireConsensusForecast) {
    $runArgs += "--require-consensus-forecast"
}
if (-not $Execute) {
    $runArgs += "--dry-run"
}

Write-Host "== LUXON priority E2E run ==" -ForegroundColor Cyan
if (-not $Execute) {
    Write-Host "Dry-run mode. Add -Execute to perform live collection." -ForegroundColor Yellow
}
& python @runArgs
