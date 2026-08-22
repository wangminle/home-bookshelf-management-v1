"""权限阶段 4 测试：B 模式逐书可见性（基线 §14 阶段 4 验收）。

验收底线：三种匿名模式可切换；切换策略不批量篡改书目；私有记录在任何
模式下都不会意外公开。覆盖：
- 兼容读取：未标记存量 = lan_shared（C 模式行为不变）；
- lan_shared 模式可见 {lan_shared, public}；explicit_public 仅 {public}；
  members_only/private 任何模式不可匿名见；disabled 全关；
- 封面与书目同可见集（防枚举）；
- Owner 端点：单书/批量设置 + 审计；非 Owner 拒绝；C→B 预览正确性；
- 迁移零数据改写（只加列）。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, Member, OperationLog
from app.services.catalog_read import effective_visibility


def _make_client(db_session: Session, host: str = "127.0.0.1") -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=(host, 50000))


@pytest.fixture()
def c_mode(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "lan_shared")
    monkeypatch.setattr(settings, "trusted_lan_cidrs", "192.168.1.0/24")


@pytest.fixture()
def seeded(client: TestClient, db_session: Session) -> dict:
    owner_id = client.get("/auth/session").json()["member_id"]
    books = {}
    for label, vis in (
        ("legacy", None), ("pub", "public"), ("mo", "members_only"), ("priv", "private"),
    ):
        b = Book(title=f"阶段4书-{label}", catalog_visibility=vis)
        db_session.add(b)
        db_session.commit()
        books[label] = b.id
    return {"owner_id": owner_id, "books": books}


def _titles(c: TestClient) -> set[str]:
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 200, r.text
    return {i["title"] for i in r.json()["data"]["items"]}


# ── 兼容读取与三模式 ──


def test_compat_rule_null_is_lan_shared() -> None:
    assert effective_visibility(None) == "lan_shared"
    assert effective_visibility("public") == "public"


def test_lan_shared_mode_shows_lan_and_public(c_mode, seeded: dict, db_session: Session) -> None:
    c = _make_client(db_session)
    titles = _titles(c)
    assert titles == {"阶段4书-legacy", "阶段4书-pub"}
    assert "阶段4书-mo" not in titles and "阶段4书-priv" not in titles


def test_explicit_public_mode_only_public(c_mode, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "explicit_public")
    c = _make_client(db_session)
    titles = _titles(c)
    assert titles == {"阶段4书-pub"}  # 未标记存量（lan_shared）也从匿名书架消失


def test_disabled_mode_blocks_all(c_mode, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "disabled")
    c = _make_client(db_session)
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403
    assert r.json()["error"] == "ANONYMOUS_CATALOG_DISABLED"


def test_detail_404_for_invisible_books(c_mode, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "explicit_public")
    c = _make_client(db_session)
    # public 可见；未标记/members_only/private 一律 404（与不存在同语义）
    assert c.get(f"/api/v1/public-catalog/books/{seeded['books']['pub']}").status_code == 200
    for label in ("legacy", "mo", "priv"):
        r = c.get(f"/api/v1/public-catalog/books/{seeded['books'][label]}")
        assert r.status_code == 404, label
        assert "阶段4书" not in r.text


def test_cover_follows_visibility(c_mode, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "explicit_public")
    c = _make_client(db_session)
    assert c.get(f"/api/v1/public-catalog/covers/{seeded['books']['pub']}").status_code == 404  # 无封面
    assert c.get(f"/api/v1/public-catalog/covers/{seeded['books']['legacy']}").status_code == 404


# ── Owner 管理端点 ──


def test_owner_single_visibility_set_and_audit(client: TestClient, seeded: dict, db_session: Session) -> None:
    r = client.patch(f"/api/v1/books/{seeded['books']['legacy']}/visibility",
                     json={"visibility": "public"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["catalog_visibility"] == "public"
    row = db_session.query(OperationLog).filter(
        OperationLog.action == "book.visibility",
    ).order_by(OperationLog.id.desc()).first()
    assert row is not None
    payload = json.loads(row.payload or "{}")
    assert payload["old_visibility"] == "lan_shared"
    assert payload["new_visibility"] == "public"
    assert payload["operator_member_id"] == seeded["owner_id"]


def test_visibility_endpoints_owner_only(client: TestClient, seeded: dict, db_session: Session) -> None:
    # 建一个 member 会话
    from app.services import agent_access
    member = Member(name="阶段4成员", role="member")
    db_session.add(member)
    db_session.commit()
    agent_access.set_member_password(db_session, member, "member-pass-12345")

    c = _make_client(db_session, "127.0.0.1")
    r = c.post("/auth/login", json={"username": member.username, "password": "member-pass-12345"})
    assert r.status_code == 200
    r = c.patch(f"/api/v1/books/{seeded['books']['pub']}/visibility", json={"visibility": "private"})
    assert r.status_code in (401, 403)
    r = c.post("/api/v1/catalog-visibility/batch",
               json={"book_ids": [seeded["books"]["pub"]], "visibility": "private"})
    assert r.status_code in (401, 403)
    r = c.get("/api/v1/catalog-visibility/preview")
    assert r.status_code in (401, 403)


def test_batch_set_with_cap_and_missing(client: TestClient, seeded: dict, db_session: Session) -> None:
    ids = [seeded["books"]["legacy"], seeded["books"]["mo"], 99999]
    r = client.post("/api/v1/catalog-visibility/batch",
                    json={"book_ids": ids, "visibility": "public"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["changed"] == 2
    assert data["missing"] == [99999]
    # 超上限拒绝
    r = client.post("/api/v1/catalog-visibility/batch",
                    json={"book_ids": list(range(1, 502)), "visibility": "public"})
    assert r.status_code == 422
    # 非法枚举拒绝
    r = client.patch(f"/api/v1/books/{seeded['books']['priv']}/visibility",
                     json={"visibility": "everyone"})
    assert r.status_code == 422


def test_preview_switch_correctness(client: TestClient, seeded: dict, db_session: Session) -> None:
    # legacy 未标记 → 将消失；pub → 继续；mo/priv → never_anonymous
    r = client.get("/api/v1/catalog-visibility/preview")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["target_mode"] == "explicit_public"
    assert data["summary"]["total"] == 4
    assert data["summary"]["remain_public"] == 1
    assert data["summary"]["disappear_from_anonymous"] == 1
    assert data["summary"]["never_anonymous"] == 2
    assert [i["id"] for i in data["remain_public"]] == [seeded["books"]["pub"]]
    assert [i["id"] for i in data["disappear"]] == [seeded["books"]["legacy"]]


def test_private_never_leaks_in_any_mode(client: TestClient, seeded: dict, db_session: Session,
                                         monkeypatch) -> None:
    from app.config import settings
    for mode in ("lan_shared", "explicit_public"):
        monkeypatch.setattr(settings, "anonymous_catalog_mode", mode)
        c = _make_client(db_session)
        titles = _titles(c)
        assert "阶段4书-priv" not in titles, mode
        assert "阶段4书-mo" not in titles, mode


def test_preview_truncated_flag_single_list(client: TestClient, db_session: Session) -> None:
    """单侧列表超 500 也必须报 truncated（修复：原按两侧合计判断会漏报）。"""
    for i in range(501):
        db_session.add(Book(title=f"截断书-{i}"))  # 未标记 = lan_shared -> 将消失
    db_session.commit()
    r = client.get("/api/v1/catalog-visibility/preview")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["summary"]["disappear_from_anonymous"] == 501  # 计数为全量
    assert len(data["disappear"]) == 500  # 列表截断
    assert data["truncated"] is True


# ── 迁移零数据改写 ──


def test_migration_adds_column_without_rewrite() -> None:
    """迁移只加列；存量 catalog_visibility 保持 NULL（兼容读取为 lan_shared）。"""
    from pathlib import Path

    migration = Path(__file__).resolve().parents[1] / "alembic/versions/i0b1c2d3e4f5_book_catalog_visibility.py"
    content = migration.read_text(encoding="utf-8")
    assert "add_column" in content
    assert "UPDATE" not in content.upper().replace("CREATE", "")  # 无数据改写语句
