[CmdletBinding()]
param(
  [string]$EnvFile = ".env.local",
  [string]$ProjectName = "luxon-local",
  [switch]$SkipBuild,
  [ValidateRange(30, 900)]
  [int]$WaitTimeoutSeconds = 240
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
  throw "Compose env file not found: $EnvPath. Create ignored .env.local first."
}
$EnvPath = (Resolve-Path -LiteralPath $EnvPath).Path

function Invoke-Docker {
  param([Parameter(Mandatory)][string[]]$Arguments)
  & docker @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "docker command failed with exit code $LASTEXITCODE"
  }
}

Invoke-Docker -Arguments @("version", "--format", "{{.Server.Version}}")

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

Invoke-Docker -Arguments ($ComposeArgs + @("config", "--quiet"))

$UpArgs = @("up", "--detach", "--wait", "--wait-timeout", $WaitTimeoutSeconds.ToString())
if (-not $SkipBuild) {
  $UpArgs += "--build"
}
Invoke-Docker -Arguments ($ComposeArgs + $UpArgs)
Invoke-Docker -Arguments ($ComposeArgs + @("ps"))

$WebUri = Get-PublishedUri -Service "web" -ContainerPort 3000 -Path "/"
$ApiUri = Get-PublishedUri -Service "api" -ContainerPort 8000 -Path "/api/health"
Write-Host "LUXON local stack is healthy."
Write-Host "Web: $WebUri"
Write-Host "API: $ApiUri"
