$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerDir = Join-Path $RepoRoot "server"
$BrowserUrl = "http://localhost:8080"

Write-Host "[GUI-ROV] Mulai..." -ForegroundColor Cyan

# Install dependency server jika belum
if (-not (Test-Path (Join-Path $ServerDir "node_modules"))) {
    Write-Host "[GUI-ROV] Installing npm dependencies..." -ForegroundColor Yellow
    Set-Location $ServerDir
    npm install
}

# Buka browser
Write-Host "[GUI-ROV] Buka browser: $BrowserUrl" -ForegroundColor Cyan
Start-Process $BrowserUrl

# Start server
Write-Host "[GUI-ROV] Server mulai di :8080" -ForegroundColor Green
Set-Location $ServerDir
npm start
