#!/usr/bin/env bash
# DEV-026 / GitHub #3：一键构建前端并 rsync --delete 同步到 backend/static/
# GitHub #5：同时构建 Skills bundle 到 backend/static/skills/（lwa 容器无 skills/ 源目录，
# bundle 必须在导入前预构建，随 backend/ 目录被 lwa import 携带）
# 用法：
#   bash scripts/deploy_frontend.sh              # 直连/后端托管，强制 VITE_BASE=/
#   bash scripts/deploy_frontend.sh --base /home-bookshelf/
#   bash scripts/deploy_frontend.sh --dry-run
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE=""
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: bash scripts/deploy_frontend.sh [--base /alias/] [--dry-run]

  --base     路径别名（自动补齐首尾 /）；省略则强制 VITE_BASE=/，避免根路径产物配别名部署
  --dry-run  只打印将执行的命令，不构建、不同步
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      BASE="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -n "$BASE" ]]; then
  BASE="/${BASE#/}"
  [[ "$BASE" == */ ]] || BASE="${BASE}/"
  VITE_BASE="$BASE"
else
  VITE_BASE="/"
fi

STATIC_DIR="$ROOT/backend/static"
DIST_DIR="$ROOT/frontend/dist"

echo "VITE_BASE=${VITE_BASE}"
echo "python3 scripts/build_skills_bundle.py --output backend/static/skills"
echo "rsync -a --delete --exclude skills/ ${DIST_DIR}/ ${STATIC_DIR}/"

if [[ "$DRY_RUN" -eq 1 ]]; then
  exit 0
fi

cd "$ROOT/frontend"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
export VITE_BASE
npm run build

if [[ ! -d "$DIST_DIR" ]]; then
  echo "构建失败：找不到 $DIST_DIR" >&2
  exit 1
fi

mkdir -p "$STATIC_DIR/skills"
python3 "$ROOT/scripts/build_skills_bundle.py" --output "$STATIC_DIR/skills"

# 排除 skills/：Skills zip 与前端产物共用 static，--delete 不得清掉 bundle
rsync -a --delete --exclude skills/ "${DIST_DIR}/" "${STATIC_DIR}/"
echo "已同步到 $STATIC_DIR"
