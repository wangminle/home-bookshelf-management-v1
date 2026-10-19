from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )


class TimestampUpdateMixin(TimestampMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )


def create_engine_from_url(database_url: str):
    if database_url.startswith("sqlite"):
        connect_args: dict = {"check_same_thread": False}
    elif database_url.startswith("postgresql"):
        # BUG-323：应用约定以 naive UTC 写入时间；会话时区固定 UTC，
        # 使 PG 把 naive 值按 UTC 解释，而不是按服务器本地时区。
        connect_args = {"options": "-c timezone=utc"}
    else:
        connect_args = {}
    return create_engine(database_url, connect_args=connect_args)
