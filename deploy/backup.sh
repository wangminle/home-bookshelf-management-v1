#!/usr/bin/env bash
# 备份 SQLite 数据库与 data/ 目录下的封面、附件
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DATA_DIR="${DATA_DIR:-${PROJECT_ROOT}/data}"
DB_FILE="${DB_FILE:-${DATA_DIR}/bookshelf.db}"

if [[ ! -f "${DB_FILE}" && -f "${PROJECT_ROOT}/backend/data/bookshelf.db" ]]; then
  DATA_DIR="${PROJECT_ROOT}/backend/data"
  DB_FILE="${DATA_DIR}/bookshelf.db"
fi
BACKUP_DIR="${BACKUP_DIR:-${PROJECT_ROOT}/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ ! -f "${DB_FILE}" ]]; then
  echo "错误：数据库不存在 ${DB_FILE}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"

DB_BACKUP="${BACKUP_DIR}/bookshelf_${STAMP}.db"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "${DB_FILE}" ".backup '${DB_BACKUP}'"
else
  echo "警告：未找到 sqlite3，将直接 cp 数据库文件；若存在 WAL 日志可能导致备份不一致" >&2
  if [[ -f "${DB_FILE}-wal" ]]; then
    echo "警告：检测到 ${DB_FILE}-wal，建议安装 sqlite3 后再备份" >&2
  fi
  cp "${DB_FILE}" "${DB_BACKUP}"
fi

ARCHIVE="${BACKUP_DIR}/data_${STAMP}.tar.gz"
tar -czf "${ARCHIVE}" -C "${DATA_DIR}" \
  --exclude='*.db-shm' \
  --exclude='*.db-wal' \
  covers attachments 2>/dev/null || true

find "${BACKUP_DIR}" -name 'bookshelf_*.db' -mtime +"${KEEP_DAYS}" -delete
find "${BACKUP_DIR}" -name 'data_*.tar.gz' -mtime +"${KEEP_DAYS}" -delete

echo "备份完成："
echo "  数据库 → ${DB_BACKUP}"
echo "  附件包 → ${ARCHIVE}"
echo "  保留 ${KEEP_DAYS} 天内的备份"
