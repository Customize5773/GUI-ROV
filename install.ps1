# Instalasi dependency GUI-ROV di laptop Windows.
param([switch]$Yolo)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "[X] '$cmd' tidak ada. $hint" -ForegroundColor Red
        exit 1
    }
    Write-Host "[ok] $cmd -> $((Get-Command $cmd).Source)" -ForegroundColor DarkGray
}

Need node "Install Node.js >= 18: https://nodejs.org (atau: winget install OpenJS.NodeJS.LTS)"
Need npm "Ikut paket Node.js."
Need python "Install Python 3.10+: https://python.org (atau: winget install Python.Python.3.12) - centang 'Add to PATH'."

Write-Host "`n[1/3] npm install (server/)" -ForegroundColor Cyan
Push-Location (Join-Path $Repo "server")
npm install
Pop-Location

Write-Host "`n[2/3] venv + pip install" -ForegroundColor Cyan
$Venv = Join-Path $Repo ".venv"
if (-not (Test-Path $Venv)) { python -m venv $Venv }
$Py = Join-Path $Venv "Scripts\python.exe"
& $Py -m pip install --upgrade pip
& $Py -m pip install -r (Join-Path $Repo "requirements.txt")
& $Py -m pip install -r (Join-Path $Repo "autonomy\requirements.txt")
if ($Yolo) { & $Py -m pip install -r (Join-Path $Repo "autonomy\requirements-laptop.txt") }

Write-Host "`n[3/3] Cek impor" -ForegroundColor Cyan
$ImportFailed = $false
foreach ($Module in @('pymavlink', 'numpy', 'cv2', 'pyzbar.pyzbar', 'zxingcpp', 'segno', 'yaml')) {
    & $Py -c "import $Module"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ok   $Module"
    } else {
        $ImportFailed = $true
        Write-Host "  GAGAL $Module" -ForegroundColor Red
    }
}
if ($ImportFailed) { Write-Host "`nAda impor yang gagal, lihat pesan di atas." -ForegroundColor Yellow; exit 1 }

if ($Yolo) {
    Write-Host "`n[VISION] Cek YOLO + GPU" -ForegroundColor Cyan
    $GpuInfo = & $Py -c "import torch, ultralytics; print('torch=' + torch.__version__ + '; CUDA=' + str(torch.cuda.is_available()) + '; GPU=' + (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'))" 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Host "[X] YOLO gagal diimpor: $GpuInfo" -ForegroundColor Red; exit 1 }
    Write-Host $GpuInfo -ForegroundColor $(if ("$GpuInfo" -match 'CUDA=True') { "Green" } else { "Yellow" })
    if ("$GpuInfo" -notmatch 'CUDA=True') {
        Write-Warning "Torch tidak melihat NVIDIA GPU. Jangan lanjut uji vision kolam sebelum driver NVIDIA/Torch CUDA benar."
    }
}

Write-Host "`nSelesai. Jalankan GUI: .\start-gui.ps1" -ForegroundColor Green
Write-Host "Script Python autonomy: .venv\Scripts\python.exe autonomy\..." -ForegroundColor DarkGray
