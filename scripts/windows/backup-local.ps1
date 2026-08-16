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

$RunningServices = @(& docker @ComposeArgs ps --services --status running)
if ($LASTEXITCODE -ne 0) {
  throw "Could not query running Compose services."
}
if ($RunningServices -notcontains "postgres") {
  throw "postgres must be running before a local backup can be created."
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupRoot = Join-Path $RepoRoot ".local-backups"
$SnapshotDir = Join-Path (Join-Path $BackupRoot "local") $Timestamp
if (Test-Path -LiteralPath $SnapshotDir) {
  throw "Backup destination already exists: $SnapshotDir"
}
New-Item -ItemType Directory -Path $SnapshotDir | Out-Null

$ContainerSnapshotDir = "/backups/local/$Timestamp"
$ContainerDumpPath = "$ContainerSnapshotDir/postgres.dump"
$HostDumpPath = Join-Path $SnapshotDir "postgres.dump"

& docker @ComposeArgs exec -T postgres sh -eu -c `
  'mkdir -p "$1" && pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --file="$2"' `
  nexus-backup $ContainerSnapshotDir $ContainerDumpPath
if ($LASTEXITCODE -ne 0) {
  throw "Postgres backup failed. The incomplete timestamp directory was preserved for inspection."
}
if (-not (Test-Path -LiteralPath $HostDumpPath -PathType Leaf)) {
  throw "Postgres reported success but no dump appeared at $HostDumpPath"
}

$ArchivePath = Join-Path $SnapshotDir "filesystem.zip"
$PersistentPaths = @(
  (Join-Path $RepoRoot "data\raw"),
  (Join-Path $RepoRoot "data\warehouse"),
  (Join-Path $RepoRoot "storage")
) | Where-Object { Test-Path -LiteralPath $_ }

if ($PersistentPaths.Count -gt 0) {
  Compress-Archive -LiteralPath $PersistentPaths -DestinationPath $ArchivePath -CompressionLevel Optimal
}

$GitHead = (& git -C $RepoRoot rev-parse HEAD 2>$null)
if ($LASTEXITCODE -ne 0) {
  $GitHead = "unknown"
}

$Manifest = [ordered]@{
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  project_name = $ProjectName
  git_head = $GitHead
  database_dump = [ordered]@{
    file = "postgres.dump"
    sha256 = (Get-FileHash -LiteralPath $HostDumpPath -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  filesystem_archive = if (Test-Path -LiteralPath $ArchivePath) {
    [ordered]@{
      file = "filesystem.zip"
      sha256 = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
      roots = @("data/raw", "data/warehouse", "storage")
    }
  } else {
    $null
  }
}

$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $SnapshotDir "manifest.json") -Encoding utf8
Write-Host "Backup created: $SnapshotDir"
Write-Host "No existing backup or persistent volume was deleted."
