"""实体书架与图书位置管理模型（LOC-04，设计：design/plans/实体书架与图书位置管理-功能分析和设计-20261005.md §6）。

结构链：Room → Shelf → Layer → Cell，照片独立关联 Shelf；副本位置落在
BookCopy.placement_*（见 book.py）。编号 code 只是可读标签，不作关联键；
层格同属一个书架由复合外键保证（shelf_layers/shelf_cells 的
(shelf_id, id) 唯一约束作为复合 FK 目标）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, TimestampUpdateMixin


class StorageRoom(Base, TimestampUpdateMixin):
    __tablename__ = "storage_rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    shelves: Mapped[list[StorageShelf]] = relationship(back_populates="room")


class StorageShelf(Base, TimestampUpdateMixin):
    __tablename__ = "storage_shelves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("storage_rooms.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    position_note: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    room: Mapped[StorageRoom] = relationship(back_populates="shelves")
    layers: Mapped[list[ShelfLayer]] = relationship(back_populates="shelf")
    cells: Mapped[list[ShelfCell]] = relationship(back_populates="shelf")
    photos: Mapped[list[ShelfPhoto]] = relationship(back_populates="shelf")


class ShelfLayer(Base, TimestampUpdateMixin):
    __tablename__ = "shelf_layers"
    __table_args__ = (
        # 复合 FK 目标：保证 cell.layer 与 cell.shelf 同属一个书架
        UniqueConstraint("shelf_id", "id", name="uq_shelf_layers_shelf_id_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shelf_id: Mapped[int] = mapped_column(ForeignKey("storage_shelves.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    shelf: Mapped[StorageShelf] = relationship(back_populates="layers")
    cells: Mapped[list[ShelfCell]] = relationship(back_populates="layer", foreign_keys="ShelfCell.layer_id")


class ShelfCell(Base, TimestampUpdateMixin):
    __tablename__ = "shelf_cells"
    __table_args__ = (
        # code 书架内唯一且不复用；(shelf_id, id) 唯一供 BookCopy 复合 FK 锚定
        UniqueConstraint("shelf_id", "code", name="uq_shelf_cells_shelf_id_code"),
        UniqueConstraint("shelf_id", "id", name="uq_shelf_cells_shelf_id_id"),
        ForeignKeyConstraint(
            ["shelf_id", "layer_id"],
            ["shelf_layers.shelf_id", "shelf_layers.id"],
            name="fk_shelf_cells_shelf_layer",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shelf_id: Mapped[int] = mapped_column(ForeignKey("storage_shelves.id"), nullable=False, index=True)
    layer_id: Mapped[int] = mapped_column(ForeignKey("shelf_layers.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    shelf: Mapped[StorageShelf] = relationship(back_populates="cells", foreign_keys="ShelfCell.shelf_id")
    layer: Mapped[ShelfLayer] = relationship(back_populates="cells", foreign_keys="ShelfCell.layer_id")


class ShelfPhoto(Base, TimestampUpdateMixin):
    __tablename__ = "shelf_photos"
    __table_args__ = (
        # 每架最多一张主图（部分唯一索引，SQLite/PostgreSQL 均支持）
        Index(
            "uq_shelf_photos_primary",
            "shelf_id",
            unique=True,
            sqlite_where=text("is_primary = 1"),
            postgresql_where=text("is_primary"),
        ),
        # 照片有物理删除：AUTOINCREMENT 防止 SQLite 复用已删除最大 ID，
        # 否则迟到的旧删除请求可凭 (复用ID, 重置version) 误删新照片
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shelf_id: Mapped[int] = mapped_column(ForeignKey("storage_shelves.id"), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    caption: Mapped[str | None] = mapped_column(String(200))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)

    shelf: Mapped[StorageShelf] = relationship(back_populates="photos")


class LocationOperation(Base, TimestampMixin):
    """位置写操作幂等回执（设计 §6.1/§7.2）：同操作者与 key 唯一，与业务修改同事务。"""
    __tablename__ = "location_operations"
    __table_args__ = (
        UniqueConstraint("operator_member_id", "idempotency_key", name="uq_location_operations_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text)


class LocationFileGcJob(Base, TimestampMixin):
    """照片删除/替换后的文件回收任务（设计 §8）：同事务登记，租约恢复。"""
    __tablename__ = "location_file_gc_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
