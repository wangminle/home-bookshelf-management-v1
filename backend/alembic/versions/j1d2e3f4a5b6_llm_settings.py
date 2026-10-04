"""Owner 后台多模态模型接口配置（单行表）。

Revision ID: j1d2e3f4a5b6
Revises: i0b1c2d3e4f5
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op

revision = "j1d2e3f4a5b6"
down_revision = "i0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("display_name", sa.String(80), nullable=False, server_default="识书模型"),
        sa.Column("base_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("api_key", sa.String(512), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="1024"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0"),
        sa.Column("image_detail", sa.String(8), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
    )


def downgrade() -> None:
    op.drop_table("llm_settings")
