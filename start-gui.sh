#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$REPO_ROOT/server"
MODE="${1:-live}"
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

