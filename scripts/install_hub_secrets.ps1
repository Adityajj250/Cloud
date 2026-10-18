[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstanceId,
    [string]$Profile = 'rift-admin',
    [string]$Region = 'ap-southeast-2',
    [string]$ExpectedAccount = '903859732456',
    [string]$ArtifactBucket = 'rift-storage-903859732456-ap-southeast-2'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $aws)) { throw 'AWS CLI was not found.' }
if ($Region -ne 'ap-southeast-2') { throw 'This AWS project is restricted to ap-southeast-2.' }

$secretDir = Join-Path $root 'config\.secrets\hub'
$files = @(
    (Join-Path $secretDir 'hub-secrets.env'),
    (Join-Path $secretDir 'hub.pem.crt'),
    (Join-Path $secretDir 'hub.private.key')
)
foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required private hub file is missing: $file" }
}
$identity = & $aws sts get-caller-identity --profile $Profile --region $Region --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'AWS sign-in/profile validation failed.' }
if ($identity.Account -ne $ExpectedAccount) { throw "Refusing wrong AWS project: expected $ExpectedAccount, received $($identity.Account)." }

$nonce = [guid]::NewGuid().ToString('N')
$tempDir = Join-Path $env:TEMP "rift-hub-secret-transfer-$nonce"
$payloadDir = Join-Path $tempDir 'payload'
$zipPath = Join-Path $tempDir 'payload.zip'
$objectKey = "rift-deploy/temporary/hub-bootstrap-$nonce.zip"
$uploaded = $false
New-Item -ItemType Directory -Path $payloadDir -Force | Out-Null
try {
    Copy-Item -LiteralPath $files[0] -Destination (Join-Path $payloadDir 'hub-secrets.env')
    Copy-Item -LiteralPath $files[1] -Destination (Join-Path $payloadDir 'hub.pem.crt')
    Copy-Item -LiteralPath $files[2] -Destination (Join-Path $payloadDir 'hub.private.key')
    Compress-Archive -Path (Join-Path $payloadDir '*') -DestinationPath $zipPath -CompressionLevel Optimal

    & $aws s3 cp $zipPath "s3://$ArtifactBucket/$objectKey" --sse AES256 --only-show-errors --profile $Profile --region $Region
    if ($LASTEXITCODE -ne 0) { throw 'Temporary encrypted S3 upload failed.' }
    $uploaded = $true

    $remoteCommands = @(
        'set -euo pipefail',
        "aws s3 cp 's3://$ArtifactBucket/$objectKey' /tmp/rift-hub-secrets.zip --region $Region --only-show-errors",
        'install -d -o root -g rift -m 0750 /etc/rift-hub/secrets /tmp/rift-hub-secret-payload',
        'unzip -oq /tmp/rift-hub-secrets.zip -d /tmp/rift-hub-secret-payload',
        'install -o root -g rift -m 0640 /tmp/rift-hub-secret-payload/hub-secrets.env /etc/rift-hub/hub-secrets.env',
        'install -o root -g rift -m 0640 /tmp/rift-hub-secret-payload/hub.pem.crt /etc/rift-hub/secrets/hub.pem.crt',
        'install -o root -g rift -m 0640 /tmp/rift-hub-secret-payload/hub.private.key /etc/rift-hub/secrets/hub.private.key',
        'rm -rf /tmp/rift-hub-secrets.zip /tmp/rift-hub-secret-payload',
        'systemctl restart rift-hub',
        'systemctl is-active rift-hub'
    )
    $parametersPath = Join-Path $tempDir 'ssm-parameters.json'
    [System.IO.File]::WriteAllText($parametersPath, (@{ commands = $remoteCommands } | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
    $commandId = & $aws ssm send-command --instance-ids $InstanceId --document-name AWS-RunShellScript --parameters "file://$parametersPath" --comment 'Install RIFT hub private configuration from temporary encrypted object' --profile $Profile --region $Region --query 'Command.CommandId' --output text
    if ($LASTEXITCODE -ne 0) { throw 'SSM secret installation command could not be sent.' }

    $deadline = [DateTime]::UtcNow.AddMinutes(5)
    do {
        Start-Sleep -Seconds 5
        $result = & $aws ssm get-command-invocation --command-id $commandId --instance-id $InstanceId --profile $Profile --region $Region --output json 2>$null | ConvertFrom-Json
        if ($LASTEXITCODE -eq 0 -and $result.Status -in @('Success','Failed','Cancelled','TimedOut','Undeliverable','Terminated')) { break }
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $result -or $result.Status -ne 'Success') {
        $detail = if ($result) { $result.StandardErrorContent } else { 'No command result was returned before timeout.' }
        throw "Hub secret installation did not succeed (status: $($result.Status)). $detail"
    }
    Write-Host "Hub secrets and dedicated IoT certificate installed on $InstanceId; rift-hub reports active."
} finally {
    if ($uploaded) {
        & $aws s3 rm "s3://$ArtifactBucket/$objectKey" --only-show-errors --profile $Profile --region $Region | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not delete temporary S3 object s3://$ArtifactBucket/$objectKey; remove it immediately." }
    }
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}
