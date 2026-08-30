# Instalasi dependency GUI-ROV di laptop Windows.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yolo   # + ultralytics/torch (~2.5 GB)
#
# Sesudah selesai: .\start-gui.ps1
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

Need node   "Install Node.js >= 18: https://nodejs.org  (atau: winget install OpenJS.NodeJS.LTS)"
Need npm    "Ikut paket Node.js."
Need python "Install Python 3.10+: https://python.org  (atau: winget install Python.Python.3.12) — centang 'Add to PATH'."

# --- Node: server dashboard ---
Write-Host "`n[1/3] npm install (server/)" -ForegroundColor Cyan
Push-Location (Join-Path $Repo "server")
npm install
Pop-Location

# --- Python: virtualenv + requirements ---
Write-Host "`n[2/3] venv + pip install" -ForegroundColor Cyan
$Venv = Join-Path $Repo ".venv"
if (-not (Test-Path $Venv)) { python -m venv $Venv }
$Py = Join-Path $Venv "Scripts\python.exe"

& $Py -m pip install --upgrade pip
& $Py -m pip install -r (Join-Path $Repo "requirements.txt")
& $Py -m pip install -r (Join-Path $Repo "autonomy\requirements.txt")
if ($Yolo) { & $Py -m pip install -r (Join-Path $Repo "autonomy\requirements-laptop.txt") }

# --- Cek impor yang gampang gagal di Windows ---
Write-Host "`n[3/3] Cek impor" -ForegroundColor Cyan
& $Py -c @"
import importlib, sys
ok = True
for m in ['pymavlink', 'numpy', 'cv2', 'pyzbar.pyzbar', 'zxingcpp', 'segno', 'yaml']:
    try:
        importlib.import_module(m)
        print('  ok  ', m)
    except Exception as e:
        ok = False
        print('  GAGAL', m, '->', e)
        if m.startswith('pyzbar'):
            print('        pyzbar butuh "Visual C++ Redistributable for VS 2013" (vcredist_x64.exe)')
sys.exit(0 if ok else 1)
"@
if ($LASTEXITCODE -ne 0) { Write-Host "`nAda impor yang gagal, lihat pesan di atas." -ForegroundColor Yellow; exit 1 }

Write-Host "`nSelesai. Jalankan GUI:  .\start-gui.ps1" -ForegroundColor Green
Write-Host "Script Python autonomy:  .venv\Scripts\python.exe autonomy\..." -ForegroundColor DarkGray
