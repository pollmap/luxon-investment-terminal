param(
  [switch]$SkipWorker,
  [switch]$SkipApi,
  [int]$WebPort = 3210,
  [int]$ApiPort = 8210,
  [string]$BuildDir = ''
)
$ErrorActionPreference = 'Stop'
$marketRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$marketPython = Join-Path $marketRoot '.venv\Scripts\python.exe'
$marketWeb = Join-Path $marketRoot 'apps\web'
$marketBuildName = if ($BuildDir) { $BuildDir } elseif ($env:NEXT_BUILD_DIR) { $env:NEXT_BUILD_DIR } else { '.next' }
$marketBuild = Join-Path $marketWeb $marketBuildName
$marketServer = Join-Path $marketBuild 'standalone\apps\web\server.js'
$marketNode = (Get-Command node -ErrorAction Stop).Source
if (!(Test-Path -LiteralPath $marketPython)) { throw 'Create .venv and install ops/market/requirements.txt first. See README-MARKET.md.' }
if (!(Test-Path -LiteralPath $marketServer)) { throw "Build apps/web into '$marketBuildName' first. See README-MARKET.md." }
$marketRoutesPath = Join-Path $marketBuild 'routes-manifest.json'
if (!(Test-Path -LiteralPath $marketRoutesPath)) { throw "Routes manifest is missing from '$marketBuildName'. Rebuild the web app first." }
$marketRoutes = Get-Content -LiteralPath $marketRoutesPath -Raw | ConvertFrom-Json
$marketRewrite = @($marketRoutes.rewrites.afterFiles) | Where-Object source -eq '/api/market/:path*' | Select-Object -First 1
$marketExpectedDestination = "http://127.0.0.1:$ApiPort/api/market/:path*"
if (!$marketRewrite -or $marketRewrite.destination -ne $marketExpectedDestination) {
  throw "The web build targets '$($marketRewrite.destination)', but -ApiPort requests $ApiPort. Rebuild with MARKET_API_BASE_URL=http://127.0.0.1:$ApiPort and NEXT_BUILD_DIR=$marketBuildName. No services were started."
}
$marketStatic = Join-Path $marketBuild 'static'
if (!(Test-Path -LiteralPath (Join-Path $marketStatic 'chunks'))) { throw "Static browser chunks are missing from '$marketBuildName'. Rebuild the web app before starting it." }
if ($SkipApi) { $marketPorts = @($WebPort) } else { $marketPorts = @($WebPort,$ApiPort) }
foreach ($marketPort in $marketPorts) {
  if (Get-NetTCPConnection -LocalPort $marketPort -State Listen -ErrorAction SilentlyContinue) { throw "Port $marketPort is in use. An existing service may already be running." }
}
$marketRunName = if ($WebPort -eq 3210) { 'run' } else { "run-$WebPort" }
$marketRun = Join-Path $marketRoot "storage\public-market\$marketRunName"
$marketPrivateEnv = Join-Path $marketRoot 'storage\public-market\runtime.env'
$marketRuntimeEnvironment = @{}
if ((!$SkipApi -or !$SkipWorker) -and (Test-Path -LiteralPath $marketPrivateEnv)) {
  foreach ($marketEntry in [IO.File]::ReadAllLines($marketPrivateEnv)) {
    if ($marketEntry -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
      $marketVariable = $Matches[1]
      if (!$marketRuntimeEnvironment.ContainsKey($marketVariable)) {
        $marketRuntimeEnvironment[$marketVariable] = [Environment]::GetEnvironmentVariable($marketVariable, 'Process')
      }
      if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($marketVariable, 'Process')) -and ![string]::IsNullOrWhiteSpace($Matches[2])) {
        [Environment]::SetEnvironmentVariable($marketVariable, $Matches[2], 'Process')
      }
    }
  }
}
New-Item -ItemType Directory -Path $marketRun -Force | Out-Null
$marketStandaloneRoot = Join-Path $marketBuild 'standalone\apps\web'
$marketPublic = Join-Path $marketStandaloneRoot 'public'
$marketStaticTarget = Join-Path (Join-Path $marketStandaloneRoot $marketBuildName) 'static'
New-Item -ItemType Directory -Path $marketPublic,$marketStaticTarget -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $marketWeb 'public') | Copy-Item -Destination $marketPublic -Recurse -Force
Get-ChildItem -LiteralPath $marketStatic | Copy-Item -Destination $marketStaticTarget -Recurse -Force
$marketProcesses = @()
if ($SkipApi -or $SkipWorker) {
  $marketProcessFile = Join-Path $marketRun 'processes.json'
  if (Test-Path -LiteralPath $marketProcessFile) {
    $marketLiveProcesses = $null
    try { $marketLiveProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop) } catch {
      # Retaining a saved ID is bookkeeping only: this startup path does not
      # signal any existing process. stop-market still verifies ownership.
      Write-Output 'Process inventory is unavailable; preserving saved IDs without controlling those processes.'
    }
    foreach ($marketSavedProcess in @(Get-Content -LiteralPath $marketProcessFile -Raw | ConvertFrom-Json)) {
      if ($null -eq $marketLiveProcesses) {
        $marketProcesses += [pscustomobject]@{ Id = [int]$marketSavedProcess.Id; ProcessName = $marketSavedProcess.ProcessName }
        continue
      }
      $marketExistingProcess = $marketLiveProcesses | Where-Object ProcessId -eq $marketSavedProcess.Id | Select-Object -First 1
      if ($marketExistingProcess -and $marketExistingProcess.CommandLine -and $marketExistingProcess.CommandLine.Contains($marketRoot)) {
        $marketProcesses += [pscustomobject]@{ Id = [int]$marketExistingProcess.ProcessId; ProcessName = $marketExistingProcess.Name }
      }
    }
  }
}
$marketPreviousPublicOrigin = $env:MARKET_PUBLIC_ORIGIN
try {
  if (!$SkipApi) {
    # This runner binds only to loopback. A saved production origin would reject
    # every preview WebSocket even though the REST pages appear to work.
    $env:MARKET_PUBLIC_ORIGIN = "http://127.0.0.1:$WebPort"
    $marketProcesses += Start-Process -FilePath $marketPython -ArgumentList @('-m','uvicorn','services.market_dashboard.app:app','--host','127.0.0.1','--port',"$ApiPort",'--ws','websockets-sansio','--no-access-log','--timeout-keep-alive','30') -WorkingDirectory $marketRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $marketRun 'api.out.log') -RedirectStandardError (Join-Path $marketRun 'api.err.log')
  }
  if (!$SkipWorker) {
    $marketProcesses += Start-Process -FilePath $marketPython -ArgumentList @('-m','services.market_dashboard.worker') -WorkingDirectory $marketRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $marketRun 'worker.out.log') -RedirectStandardError (Join-Path $marketRun 'worker.err.log')
  }
} finally {
  $env:MARKET_PUBLIC_ORIGIN = $marketPreviousPublicOrigin
  foreach ($marketVariable in $marketRuntimeEnvironment.Keys) {
    [Environment]::SetEnvironmentVariable($marketVariable, $marketRuntimeEnvironment[$marketVariable], 'Process')
  }
}
$marketPreviousPort = $env:PORT
$marketPreviousHost = $env:HOSTNAME
$marketPreviousBuildDir = $env:NEXT_BUILD_DIR
$marketPreviousApiBase = $env:MARKET_API_BASE_URL
$marketWebSensitiveVariables = @('TOSS_CLIENT_ID','TOSS_CLIENT_SECRET','DART_API_KEY','MARKET_KRX_API_KEY','MARKET_NAVER_CLIENT_ID','MARKET_NAVER_CLIENT_SECRET','MARKET_DATABASE_URL','MARKET_REDIS_URL')
$marketPreviousSensitiveEnvironment = @{}
foreach ($marketVariable in $marketWebSensitiveVariables) {
  $marketPreviousSensitiveEnvironment[$marketVariable] = [Environment]::GetEnvironmentVariable($marketVariable, 'Process')
  [Environment]::SetEnvironmentVariable($marketVariable, $null, 'Process')
}
try {
  $env:PORT="$WebPort"
  $env:HOSTNAME='127.0.0.1'
  $env:NEXT_BUILD_DIR=$marketBuildName
  $env:MARKET_API_BASE_URL="http://127.0.0.1:$ApiPort"
  $marketProcesses += Start-Process -FilePath $marketNode -ArgumentList ('"'+$marketServer+'"') -WorkingDirectory $marketWeb -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $marketRun 'web.out.log') -RedirectStandardError (Join-Path $marketRun 'web.err.log')
} finally {
  $env:PORT=$marketPreviousPort
  $env:HOSTNAME=$marketPreviousHost
  $env:NEXT_BUILD_DIR=$marketPreviousBuildDir
  $env:MARKET_API_BASE_URL=$marketPreviousApiBase
  foreach ($marketVariable in $marketWebSensitiveVariables) {
    [Environment]::SetEnvironmentVariable($marketVariable, $marketPreviousSensitiveEnvironment[$marketVariable], 'Process')
  }
}
$marketProcesses | Select-Object Id,ProcessName | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $marketRun 'processes.json')
$marketReady=$false
for($marketAttempt=0;$marketAttempt -lt 30;$marketAttempt++) {
  try { $null=Invoke-WebRequest "http://127.0.0.1:$WebPort/stocks" -TimeoutSec 2; $marketReady=$true; break } catch { Start-Sleep -Seconds 1 }
}
if(!$marketReady){throw "Services started but web readiness failed. Inspect logs in $marketRun"}
Write-Output "Stock dashboard: http://127.0.0.1:$WebPort/stocks"
Write-Output "Logs: $marketRun"
