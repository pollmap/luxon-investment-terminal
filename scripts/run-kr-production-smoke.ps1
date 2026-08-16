param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,
    [string]$Years = "2020:2025",
    [string]$Ref = "master",
    [string]$RunLabel = "",
    [string]$PartialTickers = "005930.KS",
    [switch]$Persist,
    [switch]$ForceRefresh,
    [switch]$ContinueOnError,
    [switch]$RequireConsensusForecast,
    [switch]$SyncBlob,
    [switch]$RunDeployGate,
    [switch]$PartialAudit,
    [switch]$Watch,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI is required. Install gh and authenticate with gh auth login."
}

function Assert-HttpsBaseUrl {
    param([string]$Value)

    $uri = $null
    if (-not [System.Uri]::TryCreate($Value, [System.UriKind]::Absolute, [ref]$uri)) {
        throw "BaseUrl must be an absolute HTTPS URL."
    }
    if ($uri.Scheme -ne "https") {
        throw "BaseUrl must use HTTPS for protected Vercel smoke checks."
    }
}

function Test-GitHubSecretExists {
    param([string]$Name)

    $secretJson = & gh secret list --json name 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to list GitHub Actions secrets. Confirm repo access with gh auth status."
    }
    $secrets = @($secretJson | ConvertFrom-Json)
    $matchingSecrets = @($secrets | Where-Object { $_.name -eq $Name })
    return $matchingSecrets.Count -gt 0
}

function Get-GitHubVariableValue {
    param([string]$Name)

    $value = & gh variable get $Name --json value --jq ".value" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "GitHub Actions variable $Name is required before protected API smoke."
    }
    return $value.Trim().TrimEnd("/")
}

function Assert-PartialAuditTickers {
    param(
        [bool]$IsPartialAudit,
        [string]$Value
    )

    if ($IsPartialAudit -and [string]::IsNullOrWhiteSpace($Value)) {
        throw "PartialTickers is required when -PartialAudit is set."
    }
}

Write-Host "== LUXON KR Top 10 protected API smoke workflow ==" -ForegroundColor Cyan
Write-Host "Base URL: $BaseUrl" -ForegroundColor Cyan
Write-Host "Ref: $Ref" -ForegroundColor Cyan
$SmokeMode = "full"
if ($PartialAudit) {
    $SmokeMode = "partial"
}
Assert-PartialAuditTickers -IsPartialAudit $PartialAudit.IsPresent -Value $PartialTickers
Write-Host "API smoke mode: $SmokeMode" -ForegroundColor Cyan
if ($PartialAudit) {
    Write-Host "Partial audit tickers: $PartialTickers" -ForegroundColor Cyan
}

$RunMarker = $RunLabel.Trim()
if (-not $RunMarker) {
    $RunMarker = "kr-smoke-$SmokeMode-$([DateTimeOffset]::UtcNow.ToString('yyyyMMddHHmmss'))"
}
Write-Host "Run marker: $RunMarker" -ForegroundColor Cyan

Assert-HttpsBaseUrl -Value $BaseUrl
& gh auth status 1>$null

if (-not (Test-Path ".github/workflows/kr-e2e.yml")) {
    throw ".github/workflows/kr-e2e.yml was not found."
}

& gh workflow view "kr-e2e.yml" --ref $Ref 1>$null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve kr-e2e.yml on ref '$Ref'."
}

if (-not (Test-GitHubSecretExists -Name "PF_SESSION_COOKIE")) {
    throw "GitHub Actions secret PF_SESSION_COOKIE is required before protected API smoke."
}

$TrustedBaseUrl = Get-GitHubVariableValue -Name "KR_SMOKE_BASE_URL"
$NormalizedBaseUrl = $BaseUrl.Trim().TrimEnd("/")
if (-not [string]::Equals($NormalizedBaseUrl, $TrustedBaseUrl, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "BaseUrl must exactly match the trusted KR_SMOKE_BASE_URL repository variable."
}

Write-Host "Preflight passed: gh auth, workflow, trusted HTTPS URL, and PF_SESSION_COOKIE secret are present." -ForegroundColor Green
if ($PreflightOnly) {
    exit 0
}

$workflowArgs = @(
    "workflow", "run", "kr-e2e.yml",
    "--ref", $Ref,
    "-f", "years=$Years",
    "-f", "persist=$($Persist.IsPresent.ToString().ToLowerInvariant())",
    "-f", "force_refresh=$($ForceRefresh.IsPresent.ToString().ToLowerInvariant())",
    "-f", "continue_on_error=$($ContinueOnError.IsPresent.ToString().ToLowerInvariant())",
    "-f", "require_consensus_forecast=$($RequireConsensusForecast.IsPresent.ToString().ToLowerInvariant())",
    "-f", "sync_blob=$($SyncBlob.IsPresent.ToString().ToLowerInvariant())",
    "-f", "run_deploy_gate=$($RunDeployGate.IsPresent.ToString().ToLowerInvariant())",
    "-f", "run_api_smoke=true",
    "-f", "api_smoke_mode=$SmokeMode",
    "-f", "partial_audit_tickers=$PartialTickers",
    "-f", "preview_base_url=$BaseUrl",
    "-f", "run_label=$RunMarker"
)

Write-Host "Dispatching .github/workflows/kr-e2e.yml without printing runtime secrets." -ForegroundColor Cyan
& gh @workflowArgs

if ($Watch) {
    Write-Host "Waiting for KR E2E workflow run marker '$RunMarker' to appear..." -ForegroundColor Cyan
    $run = $null
    for ($attempt = 1; $attempt -le 12; $attempt += 1) {
        $runJson = & gh run list --workflow "kr-e2e.yml" --limit 20 --json databaseId,status,conclusion,url,displayTitle,createdAt
        $runs = @($runJson | ConvertFrom-Json)
        $matchingRuns = @($runs | Where-Object { $_.displayTitle -like "*$RunMarker*" })
        if ($matchingRuns.Count -gt 0) {
            $run = $matchingRuns[0]
            break
        }
        Start-Sleep -Seconds 5
    }

    if ($null -eq $run) {
        throw "Workflow was dispatched, but no kr-e2e.yml run with marker '$RunMarker' appeared in GitHub Actions."
    }

    Write-Host "Watching run $($run.databaseId): $($run.url)" -ForegroundColor Cyan
    & gh run watch $run.databaseId --exit-status
    exit $LASTEXITCODE
}

Write-Host "Workflow dispatched. Open GitHub Actions or run:" -ForegroundColor Green
Write-Host "gh run list --workflow kr-e2e.yml --limit 3" -ForegroundColor Green
