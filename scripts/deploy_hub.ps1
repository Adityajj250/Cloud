[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$Profile = 'rift-admin',
    [string]$Region = 'ap-southeast-2',
    [string]$ExpectedAccount = '903859732456',
    [string]$StackName = 'rift-hub-sydney',
    [string]$VpcId = 'vpc-0dc175c213eb53832',
    [string]$SubnetId = 'subnet-058f4672ec467a35f',
    [string]$ArtifactBucket = 'rift-storage-903859732456-ap-southeast-2',
    [string]$HubRuntimePolicyArn
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = 'C:\Program Files\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $aws)) { throw 'AWS CLI was not found. Install AWS CLI v2 and sign in with the selected profile.' }
if ($Region -ne 'ap-southeast-2') { throw 'This AWS project is restricted to the selected Region ap-southeast-2.' }

$identity = & $aws sts get-caller-identity --profile $Profile --region $Region --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'AWS sign-in/profile validation failed.' }
if ($identity.Account -ne $ExpectedAccount) { throw "Refusing wrong AWS project: expected $ExpectedAccount, received $($identity.Account)." }

Write-Host "AWS identity: $($identity.Arn)"
Write-Host "Region: $Region"
Write-Host 'Proposed: one t3.small EC2, 30 GiB encrypted gp3, one public IPv4, CloudFront HTTPS/WSS distribution, one security group, one instance role/profile.'
Write-Host 'Inbound EC2 is limited to CloudFront origin-facing addresses on TCP 80; no SSH rule is created. Admin access uses Session Manager.'
Write-Host 'Estimated base: about USD 25.80/month before data, CPU-credit surplus, snapshots, and tax; stop/delete to control charges.'
Write-Host 'This script does not create resources unless -Apply is supplied.'
if (-not $Apply) { return }
if (-not $HubRuntimePolicyArn) { throw 'Before deployment, provide the ARN of a reviewed hub runtime IAM policy. Do not use the unscoped Autopilot baseline without review.' }

$confirm = Read-Host "Type DEPLOY to upload the package and create/update stack '$StackName' in $Region"
if ($confirm -cne 'DEPLOY') { Write-Host 'Cancelled; no deployment changes made.'; return }

Push-Location $root
try {
    & python .\scripts\package_hub.py
    if ($LASTEXITCODE -ne 0) { throw 'Hub source package creation failed.' }
    $artifactKey = 'rift-deploy/rift-hub-' + (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss') + '.zip'
    & $aws s3 cp .\dist\rift-hub.zip "s3://$ArtifactBucket/$artifactKey" --profile $Profile --region $Region
    if ($LASTEXITCODE -ne 0) { throw 'Upload to the configured artifact bucket failed.' }

    $existingStatus = & $aws cloudformation describe-stacks `
        --stack-name $StackName `
        --profile $Profile `
        --region $Region `
        --query 'Stacks[0].StackStatus' `
        --output text 2>$null
    if ($LASTEXITCODE -eq 0 -and $existingStatus -eq 'ROLLBACK_COMPLETE') {
        Write-Host "Removing the empty failed stack '$StackName' so CloudFormation can create it again."
        & $aws cloudformation delete-stack --stack-name $StackName --profile $Profile --region $Region
        if ($LASTEXITCODE -ne 0) { throw 'Could not remove the fully rolled-back stack before retrying.' }
        & $aws cloudformation wait stack-delete-complete --stack-name $StackName --profile $Profile --region $Region
        if ($LASTEXITCODE -ne 0) { throw 'Timed out waiting for the rolled-back stack to be removed.' }
    }

    & $aws cloudformation deploy `
        --template-file .\infra\ec2\hub-stack.yaml `
        --stack-name $StackName `
        --profile $Profile `
        --region $Region `
        --capabilities CAPABILITY_IAM `
        --parameter-overrides "VpcId=$VpcId" "SubnetId=$SubnetId" "HubRuntimePolicyArn=$HubRuntimePolicyArn" "ArtifactBucket=$ArtifactBucket" "ArtifactKey=$artifactKey"
    if ($LASTEXITCODE -ne 0) { throw 'CloudFormation deployment failed. Inspect the stack events before retrying.' }
    & $aws cloudformation describe-stacks --stack-name $StackName --profile $Profile --region $Region --query 'Stacks[0].Outputs' --output table
    if ($LASTEXITCODE -ne 0) { throw 'The stack completed, but output retrieval failed.' }
} finally {
    Pop-Location
}
