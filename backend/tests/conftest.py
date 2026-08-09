from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

# 测试库必须在 import app 之前定好
_TEST_DIR = Path(__file__).resolve().parent / "_tmp"
_TEST_DIR.mkdir(parents=True, exist_ok=True)
_TEST_DB = _TEST_DIR / "test_bookshelf.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["DATA_DIR"] = str(_TEST_DIR / "data")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.base import create_engine_from_url  # noqa: E402


def _run_migrations(db_url: str) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    # TST-002：script_location 在 ini 中是相对路径（alembic），解析依赖 cwd。
    # 从仓库根运行 pytest 时 cwd 不含 alembic/ 目录，导致 "Path doesn't exist: alembic"。
    # 这里钉住为 backend 目录下的绝对路径，使迁移与 cwd 无关。
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


@pytest.fixture()
def db_engine(tmp_path: Path):
    db_path = tmp_path / "case.db"
    db_url = f"sqlite:///{db_path.as_posix()}"
    os.environ["DATABASE_URL"] = db_url
    # 重建 engine 绑定到本用例库
    from app import db as db_module
    from app.config import settings

    settings.database_url = db_url
    settings.data_dir = tmp_path / "data"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "covers").mkdir(exist_ok=True)
    (settings.data_dir / "attachments").mkdir(exist_ok=True)

    engine = create_engine_from_url(db_url)
    db_module.engine = engine
    db_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    _run_migrations(db_url)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_engine, db_session: Session) -> Generator[TestClient, None, None]:
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
