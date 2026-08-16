param(
    [switch]$Overwrite,
    [switch]$IncludeDatabase
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$EnvPath = Join-Path $RepoRoot ".env.local"

function Convert-SecureStringToPlainText {
    param([Parameter(Mandatory = $true)][securestring]$SecureValue)

    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Read-SecretValue {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [switch]$Secret
    )

    if ($Secret) {
        $secure = Read-Host "$Label (leave blank to skip)" -AsSecureString
        if ($secure.Length -eq 0) {
            return $null
        }
        return Convert-SecureStringToPlainText -SecureValue $secure
    }

    $value = Read-Host "$Label (leave blank to skip)"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $null
    }
    return $value.Trim()
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Key,
        [AllowNull()][string]$Value,
        [switch]$Overwrite
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Lines
    }

    $pattern = "^\s*(export\s+)?$([regex]::Escape($Key))\s*="
    $found = $false
    $updated = foreach ($line in $Lines) {
        if ($line -match $pattern) {
            $found = $true
            if ($Overwrite) {
                "$Key=`"$($Value.Replace('`', '``').Replace('"', '\"'))`""
            }
            else {
                $line
            }
        }
        else {
            $line
        }
    }

    if (-not $found) {
        $updated += "$Key=`"$($Value.Replace('`', '``').Replace('"', '\"'))`""
    }

    return [string[]]$updated
}

if (Test-Path -LiteralPath $EnvPath) {
    $lines = [string[]](Get-Content -LiteralPath $EnvPath -Encoding UTF8)
}
else {
    $lines = [string[]]@(
        "# Local LUXON secrets. This file is ignored by git.",
        "# Values are loaded by packages.core.env.load_local_env without overriding real environment variables."
    )
}

Write-Host "LUXON local secret setup" -ForegroundColor Cyan
Write-Host "Target: $EnvPath" -ForegroundColor Cyan
Write-Host "Inputs are not echoed. Blank input keeps existing values unchanged." -ForegroundColor Yellow

$dartKey = Read-SecretValue -Label "DART_API_KEY / OpenDART key" -Secret
$fredKey = Read-SecretValue -Label "FRED_API_KEY" -Secret
$ecosKey = Read-SecretValue -Label "ECOS_API_KEY" -Secret

$lines = Set-EnvValue -Lines $lines -Key "DART_API_KEY" -Value $dartKey -Overwrite:$Overwrite
$lines = Set-EnvValue -Lines $lines -Key "FRED_API_KEY" -Value $fredKey -Overwrite:$Overwrite
$lines = Set-EnvValue -Lines $lines -Key "ECOS_API_KEY" -Value $ecosKey -Overwrite:$Overwrite

if ($IncludeDatabase) {
    $databaseUrl = Read-SecretValue -Label "DATABASE_URL" -Secret
    $dataBackend = Read-SecretValue -Label "DATA_BACKEND, usually postgres"
    $lines = Set-EnvValue -Lines $lines -Key "DATABASE_URL" -Value $databaseUrl -Overwrite:$Overwrite
    $lines = Set-EnvValue -Lines $lines -Key "DATA_BACKEND" -Value $dataBackend -Overwrite:$Overwrite
}

Set-Content -LiteralPath $EnvPath -Value $lines -Encoding UTF8

Write-Host ".env.local updated. Secret values were not printed." -ForegroundColor Green
Write-Host "Next commands:" -ForegroundColor Cyan
Write-Host "  pnpm collect:opendart:kr:005930:raw"
Write-Host "  pnpm inspect:raw:kr:005930"
Write-Host "  pnpm build:valuation-inputs:kr:005930"
