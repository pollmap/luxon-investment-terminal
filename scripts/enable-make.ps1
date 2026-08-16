$ErrorActionPreference = "Stop"

$Root = Resolve-Path "$PSScriptRoot\.."
$MakeShim = Join-Path $Root "make.cmd"

if (!(Test-Path $MakeShim)) {
  throw "make.cmd was not found at $MakeShim"
}

Set-Alias -Name make -Value $MakeShim -Scope Global

Write-Host "PowerShell alias enabled for this session."
Write-Host "You can now run: make dev"
