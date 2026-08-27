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

# Buka di jendela app-mode khusus (tanpa tab/extension lain) supaya RAM & GPU
# laptop pilot fokus ke GUI, bukan berbagi dengan tab browsing lain. F5/F12
# tetap jalan seperti biasa; fallback ke browser default kalau Edge tak ada.
Write-Host "[GUI-ROV] Buka browser (app mode): $BrowserUrl" -ForegroundColor Cyan
try {
    $proc = Start-Process msedge -ArgumentList "--app=$BrowserUrl", "--disable-extensions" -PassThru -ErrorAction Stop
    Start-Sleep -Milliseconds 500
    if ($proc -and -not $proc.HasExited) { $proc.PriorityClass = "AboveNormal" }
} catch {
    Write-Host "[GUI-ROV] Edge tidak ditemukan, buka browser default" -ForegroundColor Yellow
    Start-Process $BrowserUrl
}

# Start server
Write-Host "[GUI-ROV] Server mulai di :8080" -ForegroundColor Green
Set-Location $ServerDir
npm start
