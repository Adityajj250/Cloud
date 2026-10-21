# R.I.F.T. Worker Provisioning Tool
# Generates a new AWS IoT device identity and credentials package for a collaborator / second PC

param(
    [string]$DeviceId = "rift-win-worker-02",
    [string]$DeviceName = "Friend-Worker-PC",
    [string]$Region = "ap-southeast-2",
    [string]$Profile = "rift-admin"
)

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ">>> PROVISIONING NEW R.I.F.T. WORKER: $DeviceId <<<" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$awsCli = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
if (-not (Test-Path $awsCli)) {
    $awsCli = "aws"
}

# 1. Create Thing in AWS IoT Core
Write-Host "`n[1/5] Creating IoT Thing '$DeviceId'..." -ForegroundColor Yellow
& $awsCli iot create-thing --thing-name $DeviceId --region $Region --profile $Profile | Out-Null
Write-Host "[OK] IoT Thing '$DeviceId' ready." -ForegroundColor Green

# 2. Create Active Keys and Certificate
Write-Host "`n[2/5] Generating active X.509 certificate and private key..." -ForegroundColor Yellow
$certJson = & $awsCli iot create-keys-and-certificate --set-as-active --region $Region --profile $Profile | ConvertFrom-Json

$certArn = $certJson.certificateArn
$certId  = $certJson.certificateId
$certPem = $certJson.certificatePem
$privKey = $certJson.keyPair.PrivateKey
$pubKey  = $certJson.keyPair.PublicKey

Write-Host "[OK] Certificate generated: $certId" -ForegroundColor Green

# 3. Attach Policy and Thing Principal
Write-Host "`n[3/5] Attaching RiftDevicePolicy and binding to Thing..." -ForegroundColor Yellow
& $awsCli iot attach-policy --policy-name RiftDevicePolicy --target $certArn --region $Region --profile $Profile | Out-Null
& $awsCli iot attach-thing-principal --thing-name $DeviceId --principal $certArn --region $Region --profile $Profile | Out-Null
Write-Host "[OK] Policy attached and bound to Thing '$DeviceId'." -ForegroundColor Green

# 4. Prepare Distribution Package
Write-Host "`n[4/5] Preparing deployment files for $DeviceId..." -ForegroundColor Yellow
$outDir = Join-Path $PSScriptRoot "dist\$DeviceId"
$certsDir = Join-Path $outDir "agent\certs"

if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
New-Item -ItemType Directory -Path $certsDir -Force | Out-Null

# Save certificate and key files
Set-Content -Path (Join-Path $certsDir "device.pem.crt") -Value $certPem -NoNewline
Set-Content -Path (Join-Path $certsDir "device.private.key") -Value $privKey -NoNewline
Set-Content -Path (Join-Path $certsDir "device.public.key") -Value $pubKey -NoNewline
Set-Content -Path (Join-Path $certsDir "certificateArn.txt") -Value $certArn -NoNewline

# Copy Amazon Root CA
$localCa = Join-Path $PSScriptRoot "agent\certs\AmazonRootCA1.pem"
if (Test-Path $localCa) {
    Copy-Item $localCa (Join-Path $certsDir "AmazonRootCA1.pem") -Force
} else {
    Invoke-WebRequest -Uri "https://www.amazontrust.com/repository/AmazonRootCA1.pem" -OutFile (Join-Path $certsDir "AmazonRootCA1.pem")
}

# Create .env file for the worker
$envLines = @(
    "RIFT_DEVICE_ID=$DeviceId",
    "RIFT_DEVICE_NAME=$DeviceName",
    "AWS_DEFAULT_REGION=$Region",
    "RIFT_IOT_ENDPOINT=a30f3sdnp94spe-ats.iot.ap-southeast-2.amazonaws.com"
)
Set-Content -Path (Join-Path $outDir ".env") -Value ($envLines -join "`r`n")

# Create a double-click launcher for the friend
$batLines = @(
    "@echo off",
    "title R.I.F.T. Worker - $DeviceId",
    "echo ==================================================",
    "echo   Starting R.I.F.T. Worker Node: $DeviceId",
    "echo ==================================================",
    "set RIFT_DEVICE_ID=$DeviceId",
    "set RIFT_DEVICE_NAME=$DeviceName",
    "set AWS_DEFAULT_REGION=$Region",
    "",
    "echo [1/2] Checking Python dependencies...",
    "python -m pip install -r agent\requirements.txt --quiet",
    "",
    "echo [2/2] Connecting to AWS IoT Core...",
    "python agent\rift_agent.py",
    "pause"
)
Set-Content -Path (Join-Path $outDir "start_worker.bat") -Value ($batLines -join "`r`n")

# Create README instructions for friend
$readmeLines = @(
    "# R.I.F.T. Worker Node: $DeviceId",
    "",
    "You have been provisioned to join the R.I.F.T. distributed compute cluster!",
    "",
    "## Instructions:",
    "1. Copy the contents of this folder into your cloned repository:",
    "   - Merge the agent\certs\ folder (contains your unique X.509 cert)",
    "   - Copy start_worker.bat and .env into the root of your repo",
    "2. Double-click start_worker.bat (or run python agent\rift_agent.py).",
    "3. That is it! Your PC will make an outbound TLS connection on port 443 to AWS.",
    "   No port forwarding, no VPN, and no firewall configuration needed.",
    "4. Once connected, your device will appear as ONLINE on the mobile command center."
)
Set-Content -Path (Join-Path $outDir "README_WORKER.txt") -Value ($readmeLines -join "`r`n")

# 5. Create ZIP Archive
Write-Host "`n[5/5] Creating ZIP archive..." -ForegroundColor Yellow
$zipPath = Join-Path $PSScriptRoot "dist\$DeviceId.zip"
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path "$outDir\*" -DestinationPath $zipPath -Force

Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "[OK] SUCCESS! Worker package created at:" -ForegroundColor Green
Write-Host "  $zipPath" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Green
Write-Host "`nNEXT STEPS:" -ForegroundColor Yellow
Write-Host "1. Send $zipPath to your friend." -ForegroundColor White
Write-Host "2. Have your friend unzip it into their cloned repo and double-click start_worker.bat." -ForegroundColor White
Write-Host "3. Their PC will connect and appear in your phone Nodes tab!" -ForegroundColor White
