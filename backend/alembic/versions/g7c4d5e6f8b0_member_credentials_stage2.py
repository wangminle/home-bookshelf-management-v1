"""权限阶段 2：Member 独立凭据与成员状态字段。

- members 增加 username（登录用户名，唯一索引，大小写不敏感比对）与
  disabled_at（停用时间；非空=停用，禁止登录并撤销全部会话）；
- 新建 member_credentials（每名成员零或一条凭据，Argon2id+防爆破锁定），
  数据从 owner_credentials 平移后删除旧表（基线 §12.1：演进为成员凭据表）。

Revision ID: g7c4d5e6f8b0
Revises: f6b3c4d5e7a9
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa

revision = "g7c4d5e6f8b0"
down_revision = "f6b3c4d5e7a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("members") as batch:
        batch.add_column(sa.Column("username", sa.String(50), nullable=True))
        batch.add_column(sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_members_username", ["username"])

    # 成员凭据表（唯一约束 + 命名唯一索引同口径）
    op.create_table(
        "member_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("member_id", sa.Integer(), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
    )
    op.create_index("ix_member_credentials_member_id", "member_credentials", ["member_id"], unique=True)

    # 平移 owner 凭据并为持凭据成员补登录用户名（默认显示名，冲突时追加序号）
    conn = op.get_bind()
    creds = conn.execute(sa.text(
        "SELECT member_id, password_hash, failed_attempts, locked_until FROM owner_credentials"
    )).fetchall()
    members = {m.id: m.name for m in conn.execute(
        sa.text("SELECT id, name FROM members")
    ).fetchall()}
    used_usernames = set()
    for cred in creds:
        conn.execute(sa.text(
            "INSERT INTO member_credentials (member_id, password_hash, failed_attempts, locked_until) "
            "VALUES (:mid, :ph, :fa, :lu)"
        ), {"mid": cred.member_id, "ph": cred.password_hash,
            "fa": cred.failed_attempts, "lu": cred.locked_until})
        base = (members.get(cred.member_id) or "owner").strip() or "owner"
        username, seq = base, 2
        while username in used_usernames:
            username, seq = f"{base}_{seq}", seq + 1
        used_usernames.add(username)
        conn.execute(sa.text("UPDATE members SET username = :u WHERE id = :id"),
                     {"u": username, "id": cred.member_id})

    op.drop_table("owner_credentials")


def downgrade() -> None:
    # 不可完整回平：member_credentials 可能已含非 owner 成员凭据。
    # 回滚仅恢复表结构，业务凭据需 Owner 重新初始化。
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
    op.drop_table("member_credentials")
    with op.batch_alter_table("members") as batch:
        batch.drop_index("ix_members_username")
        batch.drop_column("disabled_at")
        batch.drop_column("username")
