"""BUG-320：真实备份脚本应保留入库照片，并能从数据库引用恢复读取。"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHOTO_PATH = "intake_photos/7/cover.12345678.jpg"
PHOTO_BYTES = b"\xff\xd8original intake photo\xff\xd9"
OTHER_FILES = {
    "covers/book.jpg": b"cover image",
    "attachments/book.epub": b"attachment content",
    "shelf_photos/3/shelf.jpg": b"shelf image",
}


def run_backup(tmp_path: Path, files: dict[str, bytes]):
    data_dir = tmp_path / "source"
    backup_dir = tmp_path / "backups"
    data_dir.mkdir()
    db_file = data_dir / "bookshelf.db"
    with sqlite3.connect(db_file) as db:
        db.execute("CREATE TABLE intake_photos (id INTEGER PRIMARY KEY, file_path TEXT)")
        if PHOTO_PATH in files:
            db.execute("INSERT INTO intake_photos VALUES (?, ?)", (1, PHOTO_PATH))
    for relative_path, content in files.items():
        path = data_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "deploy" / "backup.sh")],
        env={**os.environ, "DATA_DIR": str(data_dir), "DB_FILE": str(db_file),
             "BACKUP_DIR": str(backup_dir)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # 删除源文件后恢复，避免测试意外从原始目录读取而产生假阳性。
    shutil.rmtree(data_dir)
    restore_dir = tmp_path / "restored"
    restore_dir.mkdir()
    shutil.copy2(next(backup_dir.glob("bookshelf_*.db")), restore_dir / "bookshelf.db")
    archives = list(backup_dir.glob("data_*.tar.gz"))
    for archive in archives:
        with tarfile.open(archive) as tar:
            tar.extractall(restore_dir, filter="data")
    return result, restore_dir, archives


@pytest.mark.parametrize("with_other_files", [True, False], ids=["all-assets", "only-intake"])
def test_backup_restores_intake_photo_referenced_by_database(tmp_path, with_other_files):
    files = {**(OTHER_FILES if with_other_files else {}), PHOTO_PATH: PHOTO_BYTES}
    _, restored, archives = run_backup(tmp_path, files)
    assert len(archives) == 1, "存在入库原图时必须生成附件包"
    with sqlite3.connect(restored / "bookshelf.db") as db:
        (relative_path,) = db.execute("SELECT file_path FROM intake_photos").fetchone()
    assert (restored / relative_path).is_file(), "恢复后数据库引用的入库原图必须存在"
    assert (restored / relative_path).read_bytes() == PHOTO_BYTES
    for path, content in files.items():
        assert (restored / path).read_bytes() == content


def test_backup_accepts_legacy_assets_without_intake_directory(tmp_path):
    _, restored, archives = run_backup(tmp_path, OTHER_FILES)
    assert len(archives) == 1
    for path, content in OTHER_FILES.items():
        assert (restored / path).read_bytes() == content


def test_backup_without_asset_directories_keeps_database_and_warns(tmp_path):
    result, restored, archives = run_backup(tmp_path, {})
    assert archives == []
    assert "跳过附件包" in result.stderr
    with sqlite3.connect(restored / "bookshelf.db") as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
