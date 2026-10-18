[CmdletBinding()]
param(
    [string]$InstanceId = 'i-07959aee9e9c334e2',
    [string]$Profile = 'rift-admin',
    [string]$Region = 'ap-southeast-2',
    [string]$ExpectedAccount = '903859732456',
    [string]$ArtifactBucket = 'rift-storage-903859732456-ap-southeast-2',
    [string]$ArtifactKey
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $aws)) { throw 'AWS CLI was not found.' }
if ($Region -ne 'ap-southeast-2') { throw 'This AWS project is restricted to ap-southeast-2.' }

$identity = & $aws sts get-caller-identity --profile $Profile --region $Region --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'AWS sign-in/profile validation failed.' }
if ($identity.Account -ne $ExpectedAccount) { throw "Refusing wrong AWS project: expected $ExpectedAccount, received $($identity.Account)." }

Push-Location $root
$tempDir = Join-Path $env:TEMP ('rift-hub-update-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
try {
    if ($ArtifactKey) {
        if (-not $ArtifactKey.StartsWith('rift-deploy/')) { throw 'ArtifactKey must refer to a saved package under rift-deploy/.' }
        & $aws s3api head-object --bucket $ArtifactBucket --key $ArtifactKey --profile $Profile --region $Region | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'The selected rollback package does not exist in the artifact bucket.' }
    } else {
        & python .\scripts\package_hub.py
        if ($LASTEXITCODE -ne 0) { throw 'Hub package creation failed.' }

        $ArtifactKey = 'rift-deploy/rift-hub-update-' + (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8) + '.zip'
        & $aws s3 cp .\dist\rift-hub.zip "s3://$ArtifactBucket/$ArtifactKey" --sse AES256 --only-show-errors --profile $Profile --region $Region
        if ($LASTEXITCODE -ne 0) { throw 'Could not upload the application package to the existing artifact bucket.' }
    }

    $commands = @(
        'set -euo pipefail',
        "aws s3 cp 's3://$ArtifactBucket/$ArtifactKey' /tmp/rift-hub-update.zip --region $Region --only-show-errors",
        'unzip -oq /tmp/rift-hub-update.zip -d /opt/rift',
        'chown -R rift:rift /opt/rift',
        'rm -f /tmp/rift-hub-update.zip',
        'systemctl restart rift-hub',
        'systemctl is-active rift-hub',
        'for attempt in $(seq 1 30); do if curl --fail --silent --show-error http://127.0.0.1:8765/healthz; then exit 0; fi; sleep 1; done',
        'echo "Hub health endpoint did not become ready" >&2; exit 1'
    )
    $parametersPath = Join-Path $tempDir 'ssm-parameters.json'
    [System.IO.File]::WriteAllText($parametersPath, (@{ commands = $commands } | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))

    $commandId = & $aws ssm send-command --instance-ids $InstanceId --document-name AWS-RunShellScript --parameters "file://$parametersPath" --comment 'Update RIFT hub application code' --profile $Profile --region $Region --query 'Command.CommandId' --output text
    if ($LASTEXITCODE -ne 0) { throw 'Could not send the update through Systems Manager.' }

    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    $result = $null
    do {
        Start-Sleep -Seconds 5
        $resultJson = & $aws ssm get-command-invocation --command-id $commandId --instance-id $InstanceId --profile $Profile --region $Region --output json 2>$null
        if ($LASTEXITCODE -eq 0) {
            $result = $resultJson | ConvertFrom-Json
            if ($result.Status -in @('Success','Failed','Cancelled','TimedOut','Undeliverable','Terminated')) { break }
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    if (-not $result -or $result.Status -ne 'Success') {
        $detail = if ($result) { $result.StandardErrorContent } else { 'No command result was returned before timeout.' }
        throw "Hub code update failed (status: $($result.Status)). $detail"
    }
    Write-Host "Updated hub application on $InstanceId. SSM command: $commandId"
    Write-Host $result.StandardOutputContent
    Write-Host "Applied package: s3://$ArtifactBucket/$ArtifactKey"
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    Pop-Location
}
