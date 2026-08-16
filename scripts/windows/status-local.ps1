[CmdletBinding()]
param(
  [string]$EnvFile = ".env.local",
  [string]$ProjectName = "luxon-local"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ComposeFile = Join-Path $RepoRoot "docker-compose.yml"
$EnvPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
  $EnvFile
} else {
  Join-Path $RepoRoot $EnvFile
}

if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
  throw "Compose env file not found: $EnvPath"
}
$EnvPath = (Resolve-Path -LiteralPath $EnvPath).Path

$ComposeArgs = @(
  "compose",
  "--project-name", $ProjectName,
  "--env-file", $EnvPath,
  "--file", $ComposeFile
)

function Get-PublishedUri {
  param(
    [Parameter(Mandatory)][string]$Service,
    [Parameter(Mandatory)][int]$ContainerPort,
    [Parameter(Mandatory)][string]$Path
  )

  $Published = @(& docker @ComposeArgs port $Service "$ContainerPort/tcp")
  if ($LASTEXITCODE -ne 0 -or $Published.Count -eq 0) {
    throw "Could not resolve the published port for ${Service}:${ContainerPort}."
  }
  $Address = $Published[0].Trim()
  return "http://{0}{1}" -f $Address, $Path
}

& docker @ComposeArgs ps
if ($LASTEXITCODE -ne 0) {
  throw "docker compose ps failed with exit code $LASTEXITCODE"
}

$RunningServices = @(& docker @ComposeArgs ps --services --status running)
if ($LASTEXITCODE -ne 0) {
  throw "Could not query running Compose services."
}

$Checks = @(
  @{ Service = "api"; ContainerPort = 8000; Path = "/api/health" },
  @{ Service = "web"; ContainerPort = 3000; Path = "/" }
)
$Failed = $false

foreach ($Check in $Checks) {
  if ($RunningServices -notcontains $Check.Service) {
    Write-Warning "$($Check.Service) is not running."
    $Failed = $true
    continue
  }
  try {
    $Uri = Get-PublishedUri `
      -Service $Check.Service `
      -ContainerPort $Check.ContainerPort `
      -Path $Check.Path
    $Response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
    Write-Host "$($Check.Service): HTTP $($Response.StatusCode) $Uri"
  } catch {
    Write-Warning "$($Check.Service) health check failed: $($_.Exception.Message)"
    $Failed = $true
  }
}

if ($RunningServices -notcontains "postgres") {
  Write-Warning "postgres is not running."
  $Failed = $true
} else {
  Write-Host "postgres: running"
}

if ($Failed) {
  throw "One or more LUXON local services are not healthy."
}
