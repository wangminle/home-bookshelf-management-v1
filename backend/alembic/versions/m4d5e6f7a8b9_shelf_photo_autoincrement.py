"""BUG-302：为已执行原位置迁移的 SQLite 库补照片 ID 防复用保护。

只修改 l3 的 create_table 不能修复已经升级的库。旧表通过 batch 重建保留
行、外键及索引；已带 AUTOINCREMENT 的库不重建，不丢失 sqlite_sequence。
删除前使用过的 ID 还会保留在照片幂等回执／审计里，恢复其上界，避免空表
升级后首次上传再次分配这些旧 ID。PostgreSQL 的序列无需此修复。

Revision ID: m4d5e6f7a8b9
Revises: l3c4d5e6f7a8
Create Date: 2026-10-08
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "m4d5e6f7a8b9"
down_revision = "l3c4d5e6f7a8"
branch_labels = None
depends_on = None


def _json_object(value):
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _photo_id(value):
    # SQLite 主键是有符号 64 位整数；布尔值或损坏的非 ID 数据不作为上界。
    return value if type(value) is int and 0 < value <= 2**63 - 1 else 0


def _historical_high_water(bind):
    high_water = bind.scalar(sa.text("SELECT COALESCE(MAX(id), 0) FROM shelf_photos"))
    if bind.scalar(sa.text("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'")):
        high_water = max(high_water, bind.scalar(sa.text(
            "SELECT COALESCE(MAX(seq), 0) FROM sqlite_sequence WHERE name='shelf_photos'")))
    # 回执既包括 upload/update 的 photo.id，也包括 delete 的 photo_id。
    for value in bind.scalars(sa.text(
        "SELECT result_json FROM location_operations WHERE result_json IS NOT NULL"
    )):
        result = _json_object(value)
        photo = result.get("photo")
        high_water = max(high_water, _photo_id(result.get("photo_id")))
        if isinstance(photo, dict):
            high_water = max(high_water, _photo_id(photo.get("id")))
    # 部分历史删除未带幂等键；仍可从同事务提交的审计找回照片编号。
    for value in bind.scalars(sa.text(
        "SELECT payload FROM operation_logs WHERE action IN "
        "('storage.photo.upload', 'storage.photo.update', 'storage.photo.delete')"
    )):
        high_water = max(high_water, _photo_id(_json_object(value).get("photo_id")))
    return high_water


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    high_water = _historical_high_water(bind)
    ddl = bind.scalar(sa.text("SELECT sql FROM sqlite_master WHERE name='shelf_photos'"))
    if "AUTOINCREMENT" not in ddl.upper():
        with op.batch_alter_table(
            "shelf_photos", recreate="always",
            table_kwargs={"sqlite_autoincrement": True},
        ):
            pass
    # 表复制只知道仍存活的 ID；须把升级前的已删除 ID 一并保留到序列中。
    result = bind.execute(sa.text(
        "UPDATE sqlite_sequence SET seq = MAX(seq, :high_water) WHERE name='shelf_photos'"
    ), {"high_water": high_water})
    if result.rowcount == 0:
        bind.execute(sa.text(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('shelf_photos', :high_water)"
        ), {"high_water": high_water})


def downgrade() -> None:
    # 修订版 l3 本身已要求 AUTOINCREMENT；降回 l3 保留安全属性及 ID 上界，
    # 不重新引入迟到删除误删照片的问题。降到更早版本由 l3 删除整个位置域。
    pass
