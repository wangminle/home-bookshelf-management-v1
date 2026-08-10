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
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "错误：未找到 Python 解释器（$PY）。请安装 Python ≥3.10。" >&2
  exit 1
fi
PY_VER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_OK="$("$PY" -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')"
if [[ "$PY_OK" != "1" ]]; then
  echo "错误：需要 Python ≥3.10，当前为 $PY_VER（$PY）" >&2
  exit 1
fi
echo "==> Interpreter: $($PY --version)"

VENV=".venv"

# 检测 venv 内解释器路径（Windows/Git Bash 用 Scripts/python.exe，Linux/macOS 用 bin/python）
_detect_venv_py() {
  if [[ -f "$VENV/Scripts/python.exe" ]]; then
    echo "$VENV/Scripts/python.exe"
  else
    echo "$VENV/bin/python"
  fi
}

# BUG-128：Git Bash 下 .exe 文件可能未设可执行位，-x 测试不可靠。
# .exe 文件在 Windows 上只要存在即可执行，改用 -f 判断；非 .exe 仍用 -x。
_venv_py_ok() {
  if [[ "$1" == *.exe ]]; then
    [[ -f "$1" ]]
  else
    [[ -x "$1" ]]
  fi
}

VENV_PY="$(_detect_venv_py)"

# If the venv exists but lacks a usable interpreter for THIS platform
# (e.g. a macOS/Linux venv synced from another machine), rebuild it.
if ! _venv_py_ok "$VENV_PY"; then
  if [ -d "$VENV" ]; then
    echo "==> Found an incompatible virtualenv (missing $VENV_PY). Rebuilding..."
    rm -rf "$VENV"
  fi
  echo "==> Creating virtualenv: $VENV"
  "$PY" -m venv "$VENV"
  # BUG-128：venv 刚创建后重新检测解释器路径——Windows/Git Bash 首次创建会生成 Scripts/python.exe
  VENV_PY="$(_detect_venv_py)"
  if ! _venv_py_ok "$VENV_PY"; then
    echo "错误：venv 创建后仍未找到解释器 $VENV_PY" >&2
    exit 1
  fi
fi

echo "==> Upgrading pip"
"$VENV_PY" -m pip install --upgrade pip

echo "==> Installing dependencies (requirements.txt)"
"$VENV_PY" -m pip install -r requirements.txt

# 可选：安装开发依赖（pytest 等），供运行测试用
# Usage:  bash install.sh --dev   或   DEV_DEPS=1 bash install.sh
if [[ "${1:-}" == "--dev" || "${DEV_DEPS:-}" == "1" ]]; then
  if [[ -f requirements-dev.txt ]]; then
    echo "==> Installing dev dependencies (requirements-dev.txt)"
    "$VENV_PY" -m pip install -r requirements-dev.txt
  fi
fi

# 与 systemd/bookshelf.env.example 对齐：优先用环境变量中的 DATABASE_URL / DATA_DIR
if [[ -n "${DATABASE_URL:-}" ]]; then
  export DATABASE_URL
elif [[ -n "${DATA_DIR:-}" ]]; then
  mkdir -p "$DATA_DIR"
  export DATABASE_URL="sqlite:///${DATA_DIR}/bookshelf.db"
  export DATA_DIR
else
  mkdir -p ./data
fi
echo "==> DATABASE_URL=${DATABASE_URL:-sqlite:///./data/bookshelf.db}"
echo "==> DATA_DIR=${DATA_DIR:-./data}"

echo "==> Running database migrations (alembic upgrade head)"
"$VENV_PY" -m alembic upgrade head

cat <<EOF

[OK] Backend setup complete.
   Activate:  source .venv/bin/activate        # Linux/macOS
              .venv\\Scripts\\activate          # Windows/Git Bash
   Start:     uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --app-dir .
   Docs:      http://127.0.0.1:8000/docs

NOTE: systemd 部署请先 export DATABASE_URL / DATA_DIR（见 deploy/systemd/bookshelf.env.example），
      再运行本脚本，确保迁移库与运行库路径一致。

NOTE (barcode recognition): pyzbar needs the zbar shared library at runtime.
   macOS:  brew install zbar
   Linux:  apt-get install libzbar0
EOF
