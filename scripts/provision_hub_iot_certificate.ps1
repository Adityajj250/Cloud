[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Profile = 'rift-admin',
    [string]$Region = 'ap-southeast-2',
    [string]$ExpectedAccount = '903859732456',
    [string]$ClientId = 'rift-hub-sydney-listener',
    [string]$PolicyName = 'rift-hub-result-listener'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $aws)) { throw 'AWS CLI was not found.' }
if ($Region -ne 'ap-southeast-2') { throw 'Use the project selected Region ap-southeast-2.' }
$identity = & $aws sts get-caller-identity --profile $Profile --region $Region --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'AWS sign-in/profile validation failed.' }
if ($identity.Account -ne $ExpectedAccount) { throw "Refusing wrong AWS project: expected $ExpectedAccount, received $($identity.Account)." }

$secretDir = Join-Path $root 'config\.secrets\hub'
$certPath = Join-Path $secretDir 'hub.pem.crt'
$keyPath = Join-Path $secretDir 'hub.private.key'
$publicKeyPath = Join-Path $secretDir 'hub.public.key'
$policyPath = Join-Path $secretDir 'hub-iot-policy.json'
Write-Host "Would create one AWS IoT policy ($PolicyName) scoped to $ClientId and a separate active hub certificate/key pair in $secretDir."
Write-Host 'The certificate policy only permits this client ID to subscribe/receive RIFT result and token topics; it does not grant agent command publishing.'
Write-Host 'No action occurs unless -Apply is supplied.'
if (-not $Apply) { return }
if ((Test-Path $certPath) -or (Test-Path $keyPath) -or (Test-Path $publicKeyPath)) {
    throw "Refusing to overwrite existing hub certificate files in $secretDir. Back them up or choose a new output path first."
}
$confirm = Read-Host "Type CREATE to create policy and certificate in AWS project $ExpectedAccount"
if ($confirm -cne 'CREATE') { Write-Host 'Cancelled; no AWS resources created.'; return }

New-Item -ItemType Directory -Force $secretDir | Out-Null
$connectArn = "arn:aws:iot:${Region}:${ExpectedAccount}:client/$ClientId"
$subscribeArns = @("arn:aws:iot:${Region}:${ExpectedAccount}:topicfilter/rift/*/result",
                   "arn:aws:iot:${Region}:${ExpectedAccount}:topicfilter/rift/*/tokens")
$receiveArns = @("arn:aws:iot:${Region}:${ExpectedAccount}:topic/rift/*/result",
                 "arn:aws:iot:${Region}:${ExpectedAccount}:topic/rift/*/tokens")
$policy = @{
    Version = '2012-10-17'
    Statement = @(
        @{ Effect = 'Allow'; Action = @('iot:Connect'); Resource = @($connectArn) },
        @{ Effect = 'Allow'; Action = @('iot:Subscribe'); Resource = $subscribeArns },
        @{ Effect = 'Allow'; Action = @('iot:Receive'); Resource = $receiveArns }
    )
} | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($policyPath, $policy, [System.Text.Encoding]::ASCII)

& $aws iot create-policy --policy-name $PolicyName --policy-document "file://$policyPath" --profile $Profile --region $Region | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'IoT policy creation failed. If the name already exists, review it or choose a new PolicyName.' }
$certificateArn = & $aws iot create-keys-and-certificate --set-as-active --certificate-pem-outfile $certPath --private-key-outfile $keyPath --public-key-outfile $publicKeyPath --query certificateArn --output text --profile $Profile --region $Region
if ($LASTEXITCODE -ne 0) { throw 'Certificate creation failed. The policy may exist; inspect AWS IoT and remove unused resources manually if needed.' }
& $aws iot attach-policy --policy-name $PolicyName --target $certificateArn --profile $Profile --region $Region
if ($LASTEXITCODE -ne 0) { throw "Certificate created but policy attachment failed. Certificate ARN: $certificateArn. Attach the policy before connecting the hub." }
Write-Host "Created hub IoT certificate and attached policy '$PolicyName'."
Write-Host "Certificate ARN: $certificateArn"
Write-Host "Private key saved locally at $keyPath; never commit, email, or reuse it as a computer agent credential."
Write-Host 'Transfer the certificate and key to /etc/rift-hub/secrets/ on EC2 through an approved private channel, then delete temporary copies after confirming the hub connects.'
