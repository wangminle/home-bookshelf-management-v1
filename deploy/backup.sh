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
  # BUG-127：WAL 存在时先 checkpoint，busy 则中止，避免 .backup 得到不一致快照
  if [[ -f "${DB_FILE}-wal" ]]; then
    checkpoint_busy="$(sqlite3 "${DB_FILE}" "PRAGMA wal_checkpoint(TRUNCATE);" | cut -d'|' -f1)"
    if [[ "${checkpoint_busy}" == "1" ]]; then
      echo "错误：WAL checkpoint busy，无法获得一致快照，请重试" >&2
      exit 1
    fi
  fi
  sqlite3 "${DB_FILE}" ".backup '${DB_BACKUP}'"
elif [[ -f "${DB_FILE}-wal" ]]; then
  # BUG-127：WAL 存在且无 sqlite3 时，直接 cp 可能得到不一致快照
  # 使用 Python (本项目依赖) 的 online backup API 得到一致快照；
  # 并先 TRUNCATE checkpoint，若 busy 则以失败退出，避免静默丢数据
  PYTHON_BIN="${PYTHON_BIN:-python3}"
  if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    "${PYTHON_BIN}" -c "
import sqlite3, sys
db = '${DB_FILE}'
backup = '${DB_BACKUP}'
src = sqlite3.connect(db)
# 先尝试 checkpoint 把 WAL 合并回主库；busy=1 表示未完成，必须中止
row = src.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
if row and row[0] == 1:
    src.close()
    print('错误：WAL checkpoint busy，无法获得一致快照，请重试' , file=sys.stderr)
    sys.exit(1)
dst = sqlite3.connect(backup)
try:
    src.backup(dst)
finally:
    src.close()
    dst.close()
print('Python online backup 完成')
" || { echo "错误：Python 备份失败" >&2; exit 1; }
  else
    echo "错误：检测到 WAL 日志但无 sqlite3 且无 python3，无法安全备份" >&2
    echo "请安装 sqlite3 或 python3 后重试" >&2
    exit 1
  fi
else
  echo "警告：未找到 sqlite3，将直接 cp 数据库文件（无 WAL）" >&2
  cp "${DB_FILE}" "${DB_BACKUP}"
fi

ARCHIVE="${BACKUP_DIR}/data_${STAMP}.tar.gz"
ARCHIVE_OK=0
TAR_TARGETS=()
[[ -d "${DATA_DIR}/covers" ]] && TAR_TARGETS+=(covers)
[[ -d "${DATA_DIR}/attachments" ]] && TAR_TARGETS+=(attachments)
if [[ ${#TAR_TARGETS[@]} -eq 0 ]]; then
  echo "警告：${DATA_DIR} 下无 covers/attachments 目录，跳过附件包" >&2
  ARCHIVE="(跳过)"
else
  if tar -czf "${ARCHIVE}" -C "${DATA_DIR}" \
    --exclude='*.db-shm' \
    --exclude='*.db-wal' \
    "${TAR_TARGETS[@]}"; then
    ARCHIVE_OK=1
  else
    echo "错误：打包附件失败 ${ARCHIVE}" >&2
    rm -f "${ARCHIVE}"
    ARCHIVE="(失败)"
  fi
fi

find "${BACKUP_DIR}" -name 'bookshelf_*.db' -mtime +"${KEEP_DAYS}" -delete
find "${BACKUP_DIR}" -name 'data_*.tar.gz' -mtime +"${KEEP_DAYS}" -delete

echo "备份完成："
echo "  数据库 → ${DB_BACKUP}"
echo "  附件包 → ${ARCHIVE}"
echo "  保留 ${KEEP_DAYS} 天内的备份"
if [[ "${ARCHIVE_OK}" != "1" && "${ARCHIVE}" == "(失败)" ]]; then
  exit 1
fi
