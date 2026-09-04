$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerDir = Join-Path $RepoRoot "server"
$BrowserUrl = "http://localhost:8080"
$EnvFile = Join-Path $RepoRoot ".env"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Resolve-RepoFile($value, $default) {
    $path = if ($value) { $value } else { $default }
    if ([IO.Path]::IsPathRooted($path)) { return $path }
    return Join-Path $RepoRoot $path
}

Write-Host "[GUI-ROV] Mulai..." -ForegroundColor Cyan

# Samakan perilaku Windows dengan start-gui.sh: muat .env tanpa menimpa
# environment yang sudah diberikan operator.
if (Test-Path $EnvFile) {
    Write-Host "[GUI-ROV] Memuat .env" -ForegroundColor DarkGray
    foreach ($raw in Get-Content $EnvFile) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
        $key, $value = $line.Split("=", 2)
        if ($key -notmatch '^[A-Za-z_][A-Za-z0-9_]*$' -or -not $value) { continue }
        if (-not [Environment]::GetEnvironmentVariable($key, "Process")) {
            Set-Item -Path "Env:$key" -Value $value
        }
    }
}

if (-not $env:RPI_ADDR) { $env:RPI_ADDR = "192.168.2.2" }
if (-not $env:HOOK_VISION_PYTHON) {
    if (-not (Test-Path $VenvPython)) {
        throw "Virtualenv belum ada. Jalankan: powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yolo"
    }
    $env:HOOK_VISION_PYTHON = $VenvPython
}

$ModelPath = Resolve-RepoFile $env:HOOK_VISION_MODEL "autonomy\vision\best_pose.pt"
$CalibPath = Resolve-RepoFile $env:HOOK_VISION_CALIB "autonomy\vision\calibration\wall.npz"
$MapPath = Resolve-RepoFile $env:HOOK_VISION_MAP "autonomy\config\hook_map.pool.yaml"
foreach ($required in @($ModelPath, $CalibPath, $MapPath)) {
    if (-not (Test-Path $required)) { throw "File vision tidak ditemukan: $required" }
}

$AutonomyPath = Join-Path $RepoRoot "autonomy"
$env:PYTHONPATH = @($AutonomyPath, $env:PYTHONPATH) -ne $null -join ";"
$VisionInfo = & $env:HOOK_VISION_PYTHON -c "import sys, cv2, torch, ultralytics; from vision.hook_localization import load_calibration, load_hook_map; from vision.yolo_hook import YOLOHookDetector; YOLOHookDetector(sys.argv[1]); load_calibration(sys.argv[2]); load_hook_map(sys.argv[3]); print('READY; CUDA=' + str(torch.cuda.is_available()) + '; GPU=' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'))" $ModelPath $CalibPath $MapPath 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Dependency vision belum siap: $VisionInfo`nJalankan .\install.ps1 -Yolo"
}
$VisionInfo = "$VisionInfo"
Write-Host "[VISION] $VisionInfo" -ForegroundColor $(if ($VisionInfo -match 'CUDA=True') { "Green" } else { "Yellow" })
if ($VisionInfo -notmatch 'CUDA=True') {
    Write-Warning "CUDA tidak aktif; YOLO akan berat di CPU. Perbaiki driver/Torch sebelum uji kolam."
}

if (Test-Connection $env:RPI_ADDR -Count 1 -Quiet) {
    Write-Host "[RPI] ONLINE $($env:RPI_ADDR)" -ForegroundColor Green
} else {
    Write-Warning "Raspberry Pi $($env:RPI_ADDR) tidak dapat dijangkau. Cek Ethernet dan IPv4 laptop 192.168.2.1/24."
}

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
