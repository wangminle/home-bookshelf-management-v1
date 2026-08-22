"""权限阶段 4（B 模式）：books 增加逐书匿名可见级别。

- catalog_visibility：lan_shared / public / members_only / private；
  NULL = 兼容读取为 lan_shared（基线 §13：不在升级时批量改写存量书目）；
- 仅加列与索引，零数据改写；回滚 = 切回 anonymous_catalog_mode（环境配置），
  不依赖逆向更新。

Revision ID: i0b1c2d3e4f5
Revises: h9a0b1c2d3e4
Create Date: 2026-08-22
"""
from alembic import op
import sqlalchemy as sa

revision = "i0b1c2d3e4f5"
down_revision = "h9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column("catalog_visibility", sa.String(20), nullable=True))
        batch.create_index("ix_books_catalog_visibility", ["catalog_visibility"])


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_index("ix_books_catalog_visibility")
        batch.drop_column("catalog_visibility")
