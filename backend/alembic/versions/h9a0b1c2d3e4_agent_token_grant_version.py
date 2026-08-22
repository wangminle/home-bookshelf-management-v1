"""MCP 第二期修复：Agent 令牌绑定授权版本（BUG-213）。

- agent_tokens 增加 grant_version（NOT NULL，默认 1）：令牌签发时快照
  agent_grants.version；授权范围变更时版本递增并吊销旧令牌，且
  verify_token 校验令牌版本与当前授权版本一致，防止旧令牌继承新范围
  （或范围回收后"复活"）；
- 既有令牌回填为其所属授权的当前版本（视为已按当前范围签发；后续变更
  走新口径立即收窄）。

Revision ID: h9a0b1c2d3e4
Revises: g7c4d5e6f8b0
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "h9a0b1c2d3e4"
down_revision = "g7c4d5e6f8b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_tokens") as batch:
        batch.add_column(
            sa.Column("grant_version", sa.Integer(), nullable=False, server_default="1")
        )
    # 既有令牌回填为所属授权的当前版本
    op.execute(
        "UPDATE agent_tokens SET grant_version = COALESCE("
        "(SELECT version FROM agent_grants WHERE agent_grants.id = agent_tokens.grant_id), 1)"
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_tokens") as batch:
        batch.drop_column("grant_version")
