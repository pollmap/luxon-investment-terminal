param(
    [string]$Tickers = "005930.KS",
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

$doctorArgs = @(
    "-m", "services.ingestion_worker.cli",
    "doctor",
    "--markets", "KR"
)
if ($RequireBlob) {
    $doctorArgs += "--require-blob"
}
if ($Strict) {
    $doctorArgs += "--strict"
}

Write-Host "== LUXON KR E2E doctor ==" -ForegroundColor Cyan
& python @doctorArgs

$runArgs = @(
    "-m", "services.ingestion_worker.cli",
    "run-source-e2e",
    "--market", "KR",
    "--tickers", $Tickers,
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

Write-Host "== LUXON KR E2E run-source-e2e ==" -ForegroundColor Cyan
Write-Host "Tickers: $Tickers" -ForegroundColor Cyan
if (-not $Execute) {
    Write-Host "Dry-run mode. Add -Execute to perform live KR collection." -ForegroundColor Yellow
}
& python @runArgs
