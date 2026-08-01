#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$REPO_ROOT/server"
MODE="${1:-live}"

# Muat konfigurasi lokal kalau ada (salin dari .env.example). Variabel yang
# sudah diekspor di shell TIDAK ditimpa — env eksplisit selalu menang.
if [ -f "$REPO_ROOT/.env" ]; then
  echo "[GUI-ROV] Memuat .env"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    case "$key" in *[!A-Za-z0-9_]*|'') continue ;; esac
    # Hanya set kalau belum ada di environment dan nilainya tidak kosong.
    if [ -z "$(eval "printf '%s' \"\${$key:-}\"")" ] && [ -n "${line#*=}" ]; then
      export "$key=${line#*=}"
    fi
  done < "$REPO_ROOT/.env"
fi

RPI_ADDR="${RPI_ADDR:-192.168.2.2}"

echo "[GUI-ROV] Mode: $MODE"

if [ ! -d "$SERVER_DIR/node_modules" ]; then
  echo "[GUI-ROV] Installing npm dependencies..."
  cd "$SERVER_DIR" && npm install
fi

if [ "$MODE" = "sim" ]; then
  echo "[GUI-ROV] Starting simulation mode..."
  cd "$SERVER_DIR"
  npm run sim
else
  echo "[GUI-ROV] Checking RPI connectivity ($RPI_ADDR)..."
  if ! ping -c 1 -W 2 "$RPI_ADDR" &>/dev/null; then
    echo "[GUI-ROV] WARNING: Cannot reach RPI at $RPI_ADDR"
    echo "[GUI-ROV] Use '$0 sim' for simulation mode"
  fi
  echo "[GUI-ROV] Starting LIVE mode..."
  cd "$SERVER_DIR"
  npm start
fi

