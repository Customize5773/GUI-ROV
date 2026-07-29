#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$REPO_ROOT/server"

echo "[GUI-ROV] Mulai..."

# Install dependency server jika belum
if [ ! -d "$SERVER_DIR/node_modules" ]; then
  echo "[GUI-ROV] Installing npm dependencies..."
  cd "$SERVER_DIR" && npm install
fi

# Buka browser
BROWSER_URL="http://localhost:8080"
echo "[GUI-ROV] Buka browser: $BROWSER_URL"
if command -v xdg-open &>/dev/null; then
  xdg-open "$BROWSER_URL" 2>/dev/null || true
elif command -v open &>/dev/null; then
  open "$BROWSER_URL" 2>/dev/null || true
fi

# Start server
echo "[GUI-ROV] Server mulai di :8080"
cd "$SERVER_DIR"
npm start
