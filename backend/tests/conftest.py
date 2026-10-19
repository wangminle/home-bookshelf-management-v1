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
# CHK-039：init-password 升级场景保护需要 SETUP_TOKEN
os.environ["SETUP_TOKEN"] = "test-setup-token"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Member  # noqa: E402
from app.models.base import create_engine_from_url  # noqa: E402
from app.services import agent_access  # noqa: E402
from app.utils.member_helpers import resolve_member_id  # noqa: E402


def _run_migrations(db_url: str) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    # TST-002：script_location 在 ini 中是相对路径（alembic），解析依赖 cwd。
    # 从仓库根运行 pytest 时 cwd 不含 alembic/ 目录，导致 "Path doesn't exist: alembic"。
    # 这里钉住为 backend 目录下的绝对路径，使迁移与 cwd 无关。
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


_PG_ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


def _pg_url(db_name: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(_PG_ADMIN_URL).set(database=db_name).render_as_string(hide_password=False)


def _pg_admin(sql: str) -> None:
    """LOC-19：在管理库上执行 CREATE/DROP DATABASE（需 AUTOCOMMIT）。"""
    from sqlalchemy import create_engine, text

    admin = create_engine(_PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(sql))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def _pg_template_db():
    """TEST_DATABASE_URL 非空时，会话内只迁移一次模板库，后续每个用例 CREATE DATABASE ... TEMPLATE。"""
    if not _PG_ADMIN_URL:
        yield None
        return
    from app.config import settings
    import uuid

    template = f"hbs_test_template_{uuid.uuid4().hex}"
    # 仅清理由本次会话成功创建的库；绝不预先删除共享服务上的同名库。
    _pg_admin(f'CREATE DATABASE "{template}"')
    previous = settings.database_url
    try:
        url = _pg_url(template)
        settings.database_url = url
        try:
            _run_migrations(url)
        finally:
            settings.database_url = previous
        yield template
    finally:
        _pg_admin(f'DROP DATABASE IF EXISTS "{template}"')


@pytest.fixture()
def db_engine(tmp_path: Path, request):
    if _PG_ADMIN_URL:
        yield from _pg_db_engine(tmp_path, request.getfixturevalue("_pg_template_db"))
        return
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


def _pg_db_engine(tmp_path: Path, template: str):
    import uuid

    from app import db as db_module
    from app.config import settings

    name = f"hbs_t_{uuid.uuid4().hex[:12]}"
    _pg_admin(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
    engine = None
    try:
        db_url = _pg_url(name)
        os.environ["DATABASE_URL"] = db_url
        settings.database_url = db_url
        settings.data_dir = tmp_path / "data"
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "covers").mkdir(exist_ok=True)
        (settings.data_dir / "attachments").mkdir(exist_ok=True)

        engine = create_engine_from_url(db_url)
        db_module.engine = engine
        db_module.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        yield engine
    finally:
        try:
            if engine is not None:
                engine.dispose()
        finally:
            # 仅终止本用例独有库的残留连接，避免依赖 PG13 才支持的 DROP ... FORCE。
            _pg_admin(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                      f"WHERE datname = '{name}' AND pid <> pg_backend_pid()")
            _pg_admin(f'DROP DATABASE IF EXISTS "{name}"')


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

    # BUG-168：业务端点已全部接入 AuthContext（匿名一律 401）。
    # 默认 client 以 owner Web 会话认证，使既有用例（原本匿名可用的引导期语义）
    # 无需逐个改造；无凭证/缺 scope 行为由专门的鉴权测试覆盖。
    # 注意：不预设 owner 密码——保留「首次初始化密码」类用例的初始状态
    #（Web 会话与密码相互独立，verify_web_session 只校验会话本身）。
    member_id = resolve_member_id(db_session, None)
    member = db_session.get(Member, member_id)
    if member is None or member.role != "owner":
        member = Member(name="测试 Owner", role="owner")
        db_session.add(member)
        db_session.commit()
    session_token, _ = agent_access.create_web_session(db_session, member.id)

    with TestClient(app) as c:
        # domain 对准 TestClient 实际 host（testserver.local）：与服务器 Set-Cookie
        # 落在同一条 jar 记录上——登录会覆盖、logout 能删干净（避免夹具会话残留）
        c.cookies.set("hbs_session", session_token, domain="testserver.local")
        # verify_csrf 只对 Cookie 会话校验 Origin；loopback 在白名单内
        c.headers.update({"Origin": "http://127.0.0.1"})
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db_engine, db_session: Session) -> Generator[TestClient, None, None]:
    """无任何凭证的裸客户端（鉴权拒绝路径测试用）。"""
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
