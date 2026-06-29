"""hardening_server_defaults

Revision ID: c8d9e0f1a2b3
Revises: b7e2a1c904f3
Create Date: 2026-06-26 18:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7e2a1c904f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("members", schema=None) as batch_op:
        batch_op.alter_column("role", server_default="member")
        batch_op.alter_column("reading_streak_offset", server_default="0")

    with op.batch_alter_table("book_copies", schema=None) as batch_op:
        batch_op.alter_column("copy_type", server_default="physical")
        batch_op.alter_column("status", server_default="in_shelf")

    with op.batch_alter_table("reading_progress", schema=None) as batch_op:
        batch_op.alter_column("status", server_default="unread")
        batch_op.alter_column("to_read", server_default=sa.text("0"))
        batch_op.alter_column("owned", server_default=sa.text("1"))
        batch_op.alter_column("borrowed", server_default=sa.text("0"))

    with op.batch_alter_table("reading_logs", schema=None) as batch_op:
        batch_op.alter_column("pages_read", server_default="0")

    with op.batch_alter_table("reading_notes", schema=None) as batch_op:
        batch_op.alter_column("note_type", server_default="excerpt")

    with op.batch_alter_table("purchase_records", schema=None) as batch_op:
        batch_op.alter_column("currency", server_default="CNY")

    with op.batch_alter_table("attachments", schema=None) as batch_op:
        batch_op.alter_column("sort_order", server_default="0")

    with op.batch_alter_table("custom_fields", schema=None) as batch_op:
        batch_op.alter_column("value_type", server_default="string")


def downgrade() -> None:
    with op.batch_alter_table("custom_fields", schema=None) as batch_op:
        batch_op.alter_column("value_type", server_default=None)

    with op.batch_alter_table("attachments", schema=None) as batch_op:
        batch_op.alter_column("sort_order", server_default=None)

    with op.batch_alter_table("purchase_records", schema=None) as batch_op:
        batch_op.alter_column("currency", server_default=None)

    with op.batch_alter_table("reading_notes", schema=None) as batch_op:
        batch_op.alter_column("note_type", server_default=None)

    with op.batch_alter_table("reading_logs", schema=None) as batch_op:
        batch_op.alter_column("pages_read", server_default=None)

    with op.batch_alter_table("reading_progress", schema=None) as batch_op:
        batch_op.alter_column("borrowed", server_default=None)
        batch_op.alter_column("owned", server_default=None)
        batch_op.alter_column("to_read", server_default=None)
        batch_op.alter_column("status", server_default=None)

    with op.batch_alter_table("book_copies", schema=None) as batch_op:
        batch_op.alter_column("status", server_default=None)
        batch_op.alter_column("copy_type", server_default=None)

    with op.batch_alter_table("members", schema=None) as batch_op:
        batch_op.alter_column("reading_streak_offset", server_default=None)
        batch_op.alter_column("role", server_default=None)
