"""实体书架与图书位置管理（LOC-04，设计 §6）。

新增位置结构表（rooms/shelves/layers/cells）、书架照片、位置幂等回执与
文件回收任务；BookCopy 增加可空结构化位置字段与 placement_version。
层格同属一个书架由复合外键（shelf_cells/shelf_layers 的 (shelf_id, id)
唯一约束作为目标）保证；cell 非空必须带 shelf、仅实体副本可定位由 CHECK
约束保证；每架最多一张主图由部分唯一索引保证（SQLite/PostgreSQL 均支持）。

Revision ID: l3c4d5e6f7a8
Revises: k2b3c4d5e6f7
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op

revision = "l3c4d5e6f7a8"
down_revision = "k2b3c4d5e6f7"
branch_labels = None
depends_on = None

_TS = sa.text("(CURRENT_TIMESTAMP)")


def upgrade() -> None:
    op.create_table(
        "storage_rooms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("code", name="uq_storage_rooms_code"),
    )
    op.create_table(
        "storage_shelves",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("storage_rooms.id"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("position_note", sa.String(200), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("code", name="uq_storage_shelves_code"),
    )
    op.create_index("ix_storage_shelves_room_id", "storage_shelves", ["room_id"])
    op.create_table(
        "shelf_layers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shelf_id", sa.Integer(), sa.ForeignKey("storage_shelves.id"), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("shelf_id", "id", name="uq_shelf_layers_shelf_id_id"),
    )
    op.create_index("ix_shelf_layers_shelf_id", "shelf_layers", ["shelf_id"])
    op.create_table(
        "shelf_cells",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shelf_id", sa.Integer(), sa.ForeignKey("storage_shelves.id"), nullable=False),
        sa.Column("layer_id", sa.Integer(), sa.ForeignKey("shelf_layers.id"), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("shelf_id", "code", name="uq_shelf_cells_shelf_id_code"),
        sa.UniqueConstraint("shelf_id", "id", name="uq_shelf_cells_shelf_id_id"),
        sa.ForeignKeyConstraint(
            ["shelf_id", "layer_id"],
            ["shelf_layers.shelf_id", "shelf_layers.id"],
            name="fk_shelf_cells_shelf_layer",
        ),
    )
    op.create_index("ix_shelf_cells_shelf_id", "shelf_cells", ["shelf_id"])
    op.create_index("ix_shelf_cells_layer_id", "shelf_cells", ["layer_id"])
    op.create_table(
        "shelf_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shelf_id", sa.Integer(), sa.ForeignKey("storage_shelves.id"), nullable=False),
        sa.Column("relative_path", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(50), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("caption", sa.String(200), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
    )
    op.create_index("ix_shelf_photos_shelf_id", "shelf_photos", ["shelf_id"])
    op.create_index(
        "uq_shelf_photos_primary", "shelf_photos", ["shelf_id"], unique=True,
        sqlite_where=sa.text("is_primary = 1"), postgresql_where=sa.text("is_primary"),
    )
    op.create_table(
        "location_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operator_member_id", sa.Integer(),
                  sa.ForeignKey("members.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
        sa.UniqueConstraint("operator_member_id", "idempotency_key", name="uq_location_operations_key"),
    )
    op.create_index("ix_location_operations_operator_member_id", "location_operations", ["operator_member_id"])
    op.create_table(
        "location_file_gc_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("relative_path", sa.String(500), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_TS),
    )
    with op.batch_alter_table("book_copies") as batch:
        batch.add_column(sa.Column("placement_shelf_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("placement_cell_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("placement_version", sa.Integer(), nullable=False, server_default="1"))
        batch.create_foreign_key(
            "fk_book_copies_placement_shelf_id", "storage_shelves",
            ["placement_shelf_id"], ["id"])
        batch.create_foreign_key(
            "fk_book_copies_placement_cell", "shelf_cells",
            ["placement_shelf_id", "placement_cell_id"], ["shelf_id", "id"])
        batch.create_check_constraint(
            "ck_book_copies_placement_pair",
            "placement_cell_id IS NULL OR placement_shelf_id IS NOT NULL")
        batch.create_check_constraint(
            "ck_book_copies_placement_physical",
            "placement_shelf_id IS NULL OR copy_type = 'physical'")
        batch.create_index("ix_book_copies_placement_shelf_id", ["placement_shelf_id"])
        batch.create_index("ix_book_copies_placement_cell_id", ["placement_cell_id"])


def downgrade() -> None:
    with op.batch_alter_table("book_copies") as batch:
        batch.drop_index("ix_book_copies_placement_cell_id")
        batch.drop_index("ix_book_copies_placement_shelf_id")
        batch.drop_constraint("ck_book_copies_placement_physical", type_="check")
        batch.drop_constraint("ck_book_copies_placement_pair", type_="check")
        batch.drop_constraint("fk_book_copies_placement_cell", type_="foreignkey")
        batch.drop_constraint("fk_book_copies_placement_shelf_id", type_="foreignkey")
        batch.drop_column("placement_version")
        batch.drop_column("placement_cell_id")
        batch.drop_column("placement_shelf_id")
    op.drop_table("location_file_gc_jobs")
    op.drop_index("ix_location_operations_operator_member_id", table_name="location_operations")
    op.drop_table("location_operations")
    op.drop_index("uq_shelf_photos_primary", table_name="shelf_photos")
    op.drop_index("ix_shelf_photos_shelf_id", table_name="shelf_photos")
    op.drop_table("shelf_photos")
    op.drop_index("ix_shelf_cells_layer_id", table_name="shelf_cells")
    op.drop_index("ix_shelf_cells_shelf_id", table_name="shelf_cells")
    op.drop_table("shelf_cells")
    op.drop_index("ix_shelf_layers_shelf_id", table_name="shelf_layers")
    op.drop_table("shelf_layers")
    op.drop_index("ix_storage_shelves_room_id", table_name="storage_shelves")
    op.drop_table("storage_shelves")
    op.drop_table("storage_rooms")
