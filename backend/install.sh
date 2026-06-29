#!/usr/bin/env bash
# Backend installer for home-bookshelf (Linux / macOS / Git Bash).
# Creates the venv, installs requirements, and runs DB migrations.
# Usage:  bash backend/install.sh          # from repo root
#         bash install.sh                  # from backend/
set -euo pipefail

# Locate the backend directory (the folder this script lives in).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PY="${PY:-python3}"
echo "==> Interpreter: $($PY --version)"

VENV=".venv"
VENV_PY="$VENV/bin/python"

# If the venv exists but lacks a usable interpreter for THIS platform
# (e.g. a macOS/Linux venv synced from another machine), rebuild it.
if [ ! -x "$VENV_PY" ]; then
  if [ -d "$VENV" ]; then
    echo "==> Found an incompatible virtualenv (missing $VENV_PY). Rebuilding..."
    rm -rf "$VENV"
  fi
  echo "==> Creating virtualenv: $VENV"
  "$PY" -m venv "$VENV"
fi

echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip

echo "==> Installing dependencies (requirements.txt)"
"$VENV_PY" -m pip install -r requirements.txt

echo "==> Running database migrations (alembic upgrade head)"
"$VENV_PY" -m alembic upgrade head

cat <<EOF

[OK] Backend setup complete.
   Activate:  source .venv/bin/activate
   Start:     uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir .
   Docs:      http://127.0.0.1:8000/docs

NOTE (barcode recognition): pyzbar needs the zbar shared library at runtime.
   macOS:  brew install zbar
   Linux:  apt-get install libzbar0
EOF
