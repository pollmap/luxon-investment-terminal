[CmdletBinding()]
param(
  [string]$EnvFile = ".env.local",
  [string]$ProjectName = "luxon-local",
  [ValidateRange(1, 300)]
  [int]$TimeoutSeconds = 30
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

& docker @ComposeArgs stop --timeout $TimeoutSeconds
if ($LASTEXITCODE -ne 0) {
  throw "docker compose stop failed with exit code $LASTEXITCODE"
}

Write-Host "LUXON local services stopped. Containers and persistent data were preserved."
