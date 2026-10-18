$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$serverScript = Join-Path $projectRoot 'server\rift_server.py'
$serverOutLog = Join-Path $projectRoot 'server\public-test-server.out.log'
$serverErrLog = Join-Path $projectRoot 'server\public-test-server.err.log'

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared is required. Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/ and rerun this script."
}

$env:RIFT_SERVER_PORT = '8765'
$server = Start-Process -FilePath 'python' -ArgumentList @('"' + $serverScript + '"') -WorkingDirectory $projectRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput $serverOutLog -RedirectStandardError $serverErrLog
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { throw "R.I.F.T. server exited. See $serverOutLog and $serverErrLog" }
        try {
            $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/status' -TimeoutSec 2
            $ready = $true
            break
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "R.I.F.T. server did not become ready. See $serverOutLog and $serverErrLog" }

    Write-Host 'Starting temporary public HTTPS tunnel. Anyone with the URL can access this test server.' -ForegroundColor Yellow
    Write-Host 'Keep this window open. Press Ctrl+C to stop the tunnel and local server.'
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & cloudflared tunnel --url http://127.0.0.1:8765 2>&1 | ForEach-Object {
            $line = $_.ToString()
            if ($line -match 'https://[a-zA-Z0-9-]+\.trycloudflare\.com') {
                Write-Host "`nR.I.F.T. is reachable from any network at: $($Matches[0])`n" -ForegroundColor Green
            }
            Write-Host $line
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
