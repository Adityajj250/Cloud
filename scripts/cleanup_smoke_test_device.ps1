[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^rift-smoke-[a-f0-9]{12}$')][string]$DeviceId,
    [string]$Profile = 'rift-admin',
    [string]$Region = 'ap-southeast-2'
)

$ErrorActionPreference = 'Stop'
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if ($Region -ne 'ap-southeast-2') { throw 'This AWS project is restricted to ap-southeast-2.' }
$instanceId = & $aws cloudformation describe-stack-resource --stack-name rift-hub-sydney --logical-resource-id HubInstance --profile $Profile --region $Region --query 'StackResourceDetail.PhysicalResourceId' --output text
if ($LASTEXITCODE -ne 0) { throw 'Could not find the hub instance from CloudFormation.' }

$python = "import sqlite3; c=sqlite3.connect('/var/lib/rift/hub-devices.sqlite3'); c.execute('DELETE FROM devices WHERE device_id=?', ('$DeviceId',)); c.commit(); print('Removed one smoke-test history row')"
$encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($python))
$remote = "python3 -c `"import base64;exec(base64.b64decode('$encoded'))`""
$tempFile = Join-Path $env:TEMP ('rift-clean-smoke-' + [guid]::NewGuid().ToString('N') + '.json')
try {
    $json = @{ commands = @($remote, 'systemctl restart rift-hub', 'systemctl is-active rift-hub') } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($tempFile, $json, [System.Text.UTF8Encoding]::new($false))
    $commandId = & $aws ssm send-command --instance-ids $instanceId --document-name AWS-RunShellScript --parameters "file://$tempFile" --comment 'Remove one temporary RIFT smoke-test device record' --profile $Profile --region $Region --query 'Command.CommandId' --output text
    if ($LASTEXITCODE -ne 0) { throw 'Could not send cleanup command through Systems Manager.' }
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        Start-Sleep -Seconds 4
        $result = & $aws ssm get-command-invocation --command-id $commandId --instance-id $instanceId --profile $Profile --region $Region --output json 2>$null | ConvertFrom-Json
        if ($LASTEXITCODE -eq 0 -and $result.Status -in @('Success','Failed','Cancelled','TimedOut','Undeliverable','Terminated')) { break }
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $result -or $result.Status -ne 'Success') { throw "Smoke record cleanup failed (status: $($result.Status)). $($result.StandardErrorContent)" }
    Write-Host "Removed temporary registry record '$DeviceId' and restarted the hub."
} finally {
    Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue
}
