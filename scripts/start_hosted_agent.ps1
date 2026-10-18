[CmdletBinding()]
param(
    [string]$Profile = 'rift-admin',
    [string]$SecretsFile = 'config/.secrets/hub/hub-secrets.env',
    [string]$DeviceName,
    [string]$DeviceId,
    [string]$HubUrl = 'https://d2nv2vasdntzxl.cloudfront.net'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $localConfig = Join-Path $root 'config/local.ps1'
    if (Test-Path -LiteralPath $localConfig -PathType Leaf) { . $localConfig }

    $secretPath = if ([System.IO.Path]::IsPathRooted($SecretsFile)) { $SecretsFile } else { Join-Path $root $SecretsFile }
    if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
        throw "Shared HMAC secret file is missing: $secretPath. Create it locally with RIFT_HMAC_SECRET; never commit it."
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $secretPath) {
        if ($line -match '^\s*RIFT_HMAC_SECRET\s*=\s*(.*?)\s*$') {
            $values['RIFT_HMAC_SECRET'] = $Matches[1].Trim('"', "'")
        }
    }
    if (-not $values.ContainsKey('RIFT_HMAC_SECRET') -or $values.RIFT_HMAC_SECRET.Length -lt 32) {
        throw 'RIFT_HMAC_SECRET is missing or too short in the local shared-secret file.'
    }

    $aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
    if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
    if (-not (Test-Path -LiteralPath $aws)) { throw 'AWS CLI was not found.' }
    $account = & $aws sts get-caller-identity --profile $Profile --region ap-southeast-2 --query Account --output text
    if ($LASTEXITCODE -ne 0) { throw "AWS profile '$Profile' is not signed in or cannot access the R.I.F.T. project." }
    if ($account -ne '903859732456') { throw "AWS profile '$Profile' points to project $account, not the configured R.I.F.T. project." }

    $env:RIFT_SERVER_URL = $HubUrl.TrimEnd('/')
    $env:RIFT_HMAC_SECRET = $values.RIFT_HMAC_SECRET
    $env:RIFT_AWS_PROFILE = $Profile
    $env:RIFT_AWS_REGION = 'ap-southeast-2'
    $env:AWS_DEFAULT_REGION = 'ap-southeast-2'
    if ($DeviceName) { $env:RIFT_DEVICE_NAME = $DeviceName }
    if ($DeviceId) { $env:RIFT_DEVICE_ID = $DeviceId }

    Write-Host "Connecting this agent to $($env:RIFT_SERVER_URL). Keep this window open; Ctrl+C stops the agent."
    & python .\agent\rift_agent.py
    if ($LASTEXITCODE -ne 0) { throw 'R.I.F.T. agent exited with an error.' }
} finally {
    Pop-Location
}
