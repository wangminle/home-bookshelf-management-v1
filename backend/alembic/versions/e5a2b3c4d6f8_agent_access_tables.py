"""agent_access_tables

Revision ID: e5a2b3c4d6f8
Revises: d4f1a2b3c5e7
Create Date: 2026-08-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a2b3c4d6f8"
down_revision: Union[str, None] = "d4f1a2b3c5e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -- agent_clients --
    op.create_table(
        "agent_clients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(64), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("client_type", sa.String(50), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_agent_clients_public_id", "agent_clients", ["public_id"], unique=True)

    # -- agent_grants --
    op.create_table(
        "agent_grants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_client_id", sa.Integer(), sa.ForeignKey("agent_clients.id"), nullable=False),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_agent_grants_agent_client_id", "agent_grants", ["agent_client_id"])
    op.create_index("ix_agent_grants_member_id", "agent_grants", ["member_id"])

    # -- agent_tokens --
    op.create_table(
        "agent_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("grant_id", sa.Integer(), sa.ForeignKey("agent_grants.id"), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_tokens_token_hash", "agent_tokens", ["token_hash"], unique=True)
    op.create_index("ix_agent_tokens_grant_id", "agent_tokens", ["grant_id"])

    # -- owner_credentials --
    op.create_table(
        "owner_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_owner_credentials_member_id", "owner_credentials", ["member_id"], unique=True)

    # -- web_sessions --
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_token", sa.String(64), nullable=False, unique=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_web_sessions_session_token", "web_sessions", ["session_token"], unique=True)
    op.create_index("ix_web_sessions_member_id", "web_sessions", ["member_id"])


def downgrade() -> None:
    op.drop_table("web_sessions")
    op.drop_table("owner_credentials")
    op.drop_table("agent_tokens")
    op.drop_table("agent_grants")
    op.drop_table("agent_clients")
