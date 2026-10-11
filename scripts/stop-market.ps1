param([ValidateRange(1,65535)][int]$WebPort=3210)
$ErrorActionPreference='Stop'
$marketRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$marketRunName=if($WebPort -eq 3210){'run'}else{"run-$WebPort"}
$marketPidFile=Join-Path $marketRoot "storage\public-market\$marketRunName\processes.json"
if(!(Test-Path -LiteralPath $marketPidFile)){Write-Output 'No recorded market processes.';exit 0}
$marketAll=@(Get-CimInstance Win32_Process)
$marketSaved=@(Get-Content -LiteralPath $marketPidFile -Raw | ConvertFrom-Json)
$marketTargets=@()
foreach($marketSavedProcess in $marketSaved){
 $marketProcess=$marketAll|Where-Object ProcessId -eq $marketSavedProcess.Id
 if($marketProcess -and $marketProcess.CommandLine -and $marketProcess.CommandLine.Contains($marketRoot)){
  $marketTargets+=$marketProcess.ProcessId
  for($marketDepth=0;$marketDepth -lt 4;$marketDepth++){
   $marketChildren=@($marketAll|Where-Object{$marketTargets -contains $_.ParentProcessId -and $marketTargets -notcontains $_.ProcessId})
   if(!$marketChildren.Count){break}
   $marketTargets+=@($marketChildren.ProcessId)
  }
 }
}
foreach($marketProcessId in ($marketTargets|Select-Object -Unique)) {Stop-Process -Id $marketProcessId -Force -ErrorAction SilentlyContinue}
Write-Output "Stopped $($marketTargets.Count) processes belonging to this market workspace."
