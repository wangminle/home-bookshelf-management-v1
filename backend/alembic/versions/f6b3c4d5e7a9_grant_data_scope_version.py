"""权限阶段 1（CHK-073/BUG-197）：agent_grants 增加数据范围与版本字段。

- data_scope_json：显式数据范围（试点仅 household_shared）；NULL=历史 Grant，
  MCP 等真实数据门控对 NULL 一律拒绝（禁止旧 Grant 祖父化）；
- version：Grant 版本基线（缩权/改范围递增，完整 Token 版本绑定属阶段 3）。

Revision ID: f6b3c4d5e7a9
Revises: e5a2b3c4d6f8
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "f6b3c4d5e7a9"
down_revision = "e5a2b3c4d6f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 先加可空/带默认列（基线 §13：迁移先加字段，不批量改写业务数据）
    with op.batch_alter_table("agent_grants") as batch:
        batch.add_column(sa.Column("data_scope_json", sa.String(100), nullable=True))
        batch.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")))



def downgrade() -> None:
    with op.batch_alter_table("agent_grants") as batch:
        batch.drop_column("version")
        batch.drop_column("data_scope_json")
