$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$StateDir = Join-Path $Root ".devstack"
$PidFile = Join-Path $StateDir "pids.json"
New-Item -ItemType Directory -Force $StateDir | Out-Null

function Test-Port {
  param([int]$Port)
  try {
    $Client = [System.Net.Sockets.TcpClient]::new()
    $Async = $Client.BeginConnect("127.0.0.1", $Port, $null, $null)
    $Connected = $Async.AsyncWaitHandle.WaitOne(300)
    if ($Connected) {
      $Client.EndConnect($Async)
    }
    $Client.Close()
    return $Connected
  } catch {
    return $false
  }
}

Write-Host "Starting Personal FAST Graph-style terminal dev stack..."
Write-Host "API:  http://127.0.0.1:8000"
Write-Host "Web:  http://127.0.0.1:3000"

$Api = $null
$Web = $null
if (Test-Port 8000) {
  Write-Host "API port 8000 is already active; reusing it."
} else {
  $Api = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList "-NoExit", "-Command", "cd '$Root'; python -m uvicorn services.api.main:app --reload --port 8000"
}
if (Test-Port 3000) {
  Write-Host "Web port 3000 is already active; reusing it."
} else {
  $Web = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList "-NoExit", "-Command", "cd '$Root'; pnpm --filter @personal-fastgraphs/web dev"
}

@{
  api = if ($Api) { $Api.Id } else { $null }
  web = if ($Web) { $Web.Id } else { $null }
  started_at = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Encoding utf8 $PidFile

Write-Host "Dev stack is available."
Write-Host "PID file: $PidFile"
