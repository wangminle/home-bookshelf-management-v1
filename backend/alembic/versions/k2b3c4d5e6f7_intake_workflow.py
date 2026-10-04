"""拍照批量入库协作对象（PLN-012 M3 / BI-12）。

WorkItem/Photo/Candidate/ChangeSet/Decision/CommandExecution 最小切片；
CommandExecution.command_key 唯一约束承载幂等（BI-15）。

Revision ID: k2b3c4d5e6f7
Revises: j1d2e3f4a5b6
Create Date: 2026-10-03
"""
import sqlalchemy as sa
from alembic import op

revision = "k2b3c4d5e6f7"
down_revision = "j1d2e3f4a5b6"
branch_labels = None
depends_on = None

_TS = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    op.create_table(
        "intake_work_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_member_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
    )
    op.create_table(
        "intake_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_item_id", sa.Integer(), sa.ForeignKey("intake_work_items.id"), nullable=False),
        sa.Column("photo_id", sa.String(64), nullable=False),
        sa.Column("file_path", sa.String(300), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("role", sa.String(10), nullable=False, server_default="unknown"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("work_item_id", "photo_id", name="uq_intake_photo_pid"),
    )
    op.create_index("ix_intake_photos_work_item_id", "intake_photos", ["work_item_id"])
    op.create_index("ix_intake_photos_sha256", "intake_photos", ["sha256"])
    op.create_table(
        "intake_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_item_id", sa.Integer(), sa.ForeignKey("intake_work_items.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_review"),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("subtitle", sa.String(500), nullable=True),
        sa.Column("authors", sa.Text(), nullable=True),
        sa.Column("isbn", sa.String(20), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("conflicts", sa.Text(), nullable=True),
        sa.Column("photo_ids", sa.Text(), nullable=True),
        sa.Column("match_book_id", sa.Integer(), nullable=True),
        sa.Column("match_diff", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
    )
    op.create_index("ix_intake_candidates_work_item_id", "intake_candidates", ["work_item_id"])
    op.create_table(
        "intake_change_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_item_id", sa.Integer(), sa.ForeignKey("intake_work_items.id"), nullable=False),
        sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("intake_candidates.id"), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_by_member_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("candidate_id", "candidate_version", name="uq_intake_cs_candidate_version"),
    )
    op.create_index("ix_intake_change_sets_work_item_id", "intake_change_sets", ["work_item_id"])
    op.create_table(
        "intake_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("work_item_id", sa.Integer(), sa.ForeignKey("intake_work_items.id"), nullable=False),
        sa.Column("change_set_id", sa.Integer(), sa.ForeignKey("intake_change_sets.id"), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("candidate_version", sa.Integer(), nullable=False),
        sa.Column("decided_by_member_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(10), nullable=False, server_default="confirm"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
    )
    op.create_index("ix_intake_decisions_work_item_id", "intake_decisions", ["work_item_id"])
    op.create_table(
        "intake_command_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("command_key", sa.String(64), nullable=False),
        sa.Column("change_set_id", sa.Integer(), sa.ForeignKey("intake_change_sets.id"), nullable=False),
        sa.Column("command_type", sa.String(40), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("book_id", sa.Integer(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by_member_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
    )
    # 幂等键唯一（BI-15）：同键同参数返回既有回执，同键不同参数拒绝
    op.create_index("ix_intake_command_executions_command_key", "intake_command_executions",
                    ["command_key"], unique=True)
    op.create_index("ix_intake_command_executions_change_set_id", "intake_command_executions", ["change_set_id"])


def downgrade() -> None:
    op.drop_table("intake_command_executions")
    op.drop_table("intake_decisions")
    op.drop_table("intake_change_sets")
    op.drop_table("intake_candidates")
    op.drop_table("intake_photos")
    op.drop_table("intake_work_items")
