[CmdletBinding()]
param(
  [string]$EnvFile = ".env.local",
  [string]$ProjectName = "luxon-local",
  [string]$TaskName = "LUXON Local Backup",
  [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
  [string]$At = "02:00",
  [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BackupScript = (Resolve-Path (Join-Path $PSScriptRoot "backup-local.ps1")).Path
$EnvPath = if ([System.IO.Path]::IsPathRooted($EnvFile)) {
  $EnvFile
} else {
  Join-Path $RepoRoot $EnvFile
}

if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
  throw "Compose env file not found: $EnvPath"
}
$EnvPath = (Resolve-Path -LiteralPath $EnvPath).Path

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask -and -not $Force) {
  throw "Scheduled task '$TaskName' already exists. Re-run with -Force only if replacement is intended."
}

$PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$ActionArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -EnvFile "{1}" -ProjectName "{2}"' -f `
  $BackupScript, $EnvPath, $ProjectName
$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $ActionArguments -WorkingDirectory $RepoRoot
$TriggerTime = [datetime]::ParseExact($At, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
$Trigger = New-ScheduledTaskTrigger -Daily -At $TriggerTime
$Settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Principal $Principal `
  -Description "Creates an additive LUXON Postgres and local data backup. Docker Desktop must be running."

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force:$Force | Out-Null
Write-Host "Scheduled task installed: $TaskName at $At for $CurrentUser"
Write-Host "This installer is never called by the start script; registration occurred only because this script was run explicitly."
