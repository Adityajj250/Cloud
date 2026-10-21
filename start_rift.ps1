# R.I.F.T. One-Click Launcher
# Starts both the Agent Daemon and HTTPS Server in dedicated terminal windows

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ">>> STARTING R.I.F.T. SYSTEM <<<" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Check AWS Login Session
Write-Host "`n[1/3] Checking AWS project session (rift-admin)..." -ForegroundColor Yellow
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

$sts = & "C:\Program Files\Amazon\AWSCLIV2\aws.exe" sts get-caller-identity --profile rift-admin 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "AWS session expired or not logged in. Opening browser to login..." -ForegroundColor Yellow
    & "C:\Program Files\Amazon\AWSCLIV2\aws.exe" login --region ap-southeast-2 --profile rift-admin
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Login was not completed. Please try again." -ForegroundColor Red
        Pause
        Exit
    }
} else {
    Write-Host "AWS session is active." -ForegroundColor Green
}

# 2. Detect Local IP
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notmatch "Loopback|vEthernet" -and $_.IPAddress -notmatch "^169\.254\." } | Select-Object -First 1).IPAddress
Write-Host "`n[2/3] Active LAN IP detected: $ip" -ForegroundColor Yellow

# 3. Launch Agent and Server in separate windows
Write-Host "`n[3/3] Launching Worker Agent and HTTPS Server..." -ForegroundColor Yellow

Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; Write-Host '--- R.I.F.T. WORKER AGENT ---' -ForegroundColor Cyan; python agent\rift_agent.py"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; Write-Host '--- R.I.F.T. HTTPS SERVER ---' -ForegroundColor Green; python server\rift_server.py"

Write-Host "`n==================================================" -ForegroundColor Green
Write-Host "R.I.F.T. is now running!" -ForegroundColor Green
Write-Host "On your Android Phone (same Wi-Fi), open Chrome:" -ForegroundColor White
Write-Host "  https://${ip}:8443" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Green
