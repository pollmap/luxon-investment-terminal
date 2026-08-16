$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$PidFile = Join-Path $Root ".devstack\pids.json"

if (!(Test-Path $PidFile)) {
  Write-Host "No dev stack PID file found."
  exit 0
}

$State = Get-Content -Raw $PidFile | ConvertFrom-Json

function Stop-ProcessTree {
  param([int]$RootPid)
  $Children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $RootPid }
  foreach ($Child in $Children) {
    Stop-ProcessTree -RootPid $Child.ProcessId
  }
  if (Get-Process -Id $RootPid -ErrorAction SilentlyContinue) {
    Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
  }
}

foreach ($Name in @("api", "web")) {
  $PidValue = $State.$Name
  if ($PidValue -and (Get-Process -Id $PidValue -ErrorAction SilentlyContinue)) {
    Stop-ProcessTree -RootPid $PidValue
    Write-Host "Stopped $Name process $PidValue"
  }
}

Remove-Item $PidFile -Force
Write-Host "Dev stack stopped."
