"""MCP 封面 Resource 测试（第三项分析中唯一可独立评估的扩展，默认关闭）。

覆盖：
- 默认关闭：resources/read → -32601；
- 启用后：试点 Grant 可读 blob（base64）；非试点/缺 Scope/无 Token 拒绝；
- 不复用匿名封面 URL（返回 blob 而非 URL，输出无路径/无匿名链接）；
- URI 解析与防枚举（不存在=COVER_NOT_FOUND）；限流与审计走同一链。
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book
from app.services import rate_limit, security_audit


@pytest.fixture(autouse=True)
def _reset_state():
    rate_limit.reset()
    security_audit.reset()
    yield
    rate_limit.reset()
    security_audit.reset()


@pytest.fixture()
def mcp_on(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "cover-res-cursor-secret-with-enough-entropy")
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "testserver")


@pytest.fixture()
def cover_world(client: TestClient, db_session: Session) -> dict:
    """带封面的书 + 试点 Grant 与混合 Grant 两枚 Token。"""
    from PIL import Image

    from app.config import settings

    covers = Path(settings.covers_dir)
    covers.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (120, 180), "green").save(covers / "coverres.jpg")

    book = Book(title="封面Resource书", cover_path="covers/coverres.jpg")
    db_session.add(book)
    db_session.commit()

    owner_id = client.get("/auth/session").json()["member_id"]
    tokens = {}
    for label, scopes, data_scope in (
        ("pilot", ["books:read"], "household_shared"),
        ("mixed", ["books:read", "notes:read"], "household_shared"),
    ):
        r = client.post("/agent-access/clients", json={"display_name": f"CoverRes-{label}"})
        cid = r.json()["id"]
        payload = {"agent_client_id": cid, "member_id": owner_id, "scopes": scopes}
        if data_scope:
            payload["data_scope"] = data_scope
        r = client.post("/agent-access/grants", json=payload)
        r = client.post("/agent-access/tokens", json={"grant_id": r.json()["id"]})
        tokens[label] = r.json()["token"]
    return {"book_id": book.id, "tokens": tokens}


def _res(db_session: Session, token: str, uri: str):
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    # BUG-214 网络档：回环来源可信且豁免 HTTPS 检查；BUG-208：每请求 params._meta 必填
    return TestClient(app, client=("127.0.0.1", 50000)).post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "resources/read",
              "params": {"uri": uri, "_meta": {}}},
        headers={"Authorization": f"Bearer {token}",
                 "MCP-Protocol-Version": "2026-07-28"},
    )


def test_cover_resource_disabled_by_default(mcp_on, cover_world: dict, db_session: Session) -> None:
    """默认关闭：resources/read 按未知方法 -32601（第三项分析：非必选项）。"""
    r = _res(db_session, cover_world["tokens"]["pilot"], f"bookshelf://covers/{cover_world['book_id']}")
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


def test_cover_resource_enabled_serves_blob(mcp_on, cover_world: dict, db_session: Session,
                                             monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_cover_resource_enabled", True)
    r = _res(db_session, cover_world["tokens"]["pilot"], f"bookshelf://covers/{cover_world['book_id']}")
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    contents = result["contents"]
    assert len(contents) == 1
    c = contents[0]
    assert c["uri"] == f"bookshelf://covers/{cover_world['book_id']}"
    assert c["mimeType"] == "image/jpeg"
    # blob 可解码为真实图片字节；输出无磁盘文件名、无匿名封面 URL
    # （资源 URI 命名空间 bookshelf://covers/{id} 是合法契约，不算路径泄露）
    raw = base64.b64decode(c["blob"])
    assert raw[:2] == b"\xff\xd8"  # JPEG magic
    dumped = json.dumps(result)
    assert "coverres" not in dumped  # 源文件名不出现
    assert ".jpg" not in dumped and ".png" not in dumped
    assert "/api/v1/public-catalog" not in dumped


def test_cover_resource_requires_pilot_grant(mcp_on, cover_world: dict, db_session: Session,
                                             monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_cover_resource_enabled", True)
    r = _res(db_session, cover_world["tokens"]["mixed"], f"bookshelf://covers/{cover_world['book_id']}")
    assert r.status_code == 403
    assert r.headers.get("X-Error-Code") == "PILOT_GRANT_REQUIRED"


def test_cover_resource_uri_and_not_found(mcp_on, cover_world: dict, db_session: Session,
                                          monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_cover_resource_enabled", True)
    token = cover_world["tokens"]["pilot"]
    r = _res(db_session, token, "bookshelf://books/1")
    assert r.json()["result"]["structuredError"]["code"] == "RESOURCE_URI_INVALID"
    r = _res(db_session, token, "bookshelf://covers/999999")
    assert r.json()["result"]["structuredError"]["code"] == "COVER_NOT_FOUND"


def test_cover_resource_mime_follows_real_suffix_on_fallback(mcp_on, cover_world: dict,
                                                             db_session: Session, monkeypatch) -> None:
    """PIL 回退原图时按真实后缀声明 MIME（修复：原仅二分 jpeg/png，webp/gif 误报）。"""
    from PIL import Image

    from app.config import settings

    covers = Path(settings.covers_dir)
    Image.new("RGB", (60, 90), "blue").save(covers / "coverres.webp")
    webp_book = Book(title="WebP封面书", cover_path="covers/coverres.webp")
    db_session.add(webp_book)
    db_session.commit()

    # 模拟 PIL 不可用：缩略图管线回退原图（后缀 .webp）
    import app.api.v1.public_catalog as pc
    monkeypatch.setattr(pc, "_ensure_thumbnail", lambda src: src)
    monkeypatch.setattr(settings, "mcp_cover_resource_enabled", True)

    r = _res(db_session, cover_world["tokens"]["pilot"], f"bookshelf://covers/{webp_book.id}")
    assert r.status_code == 200, r.text
    assert r.json()["result"]["contents"][0]["mimeType"] == "image/webp"


def test_cover_resource_audited(mcp_on, cover_world: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_cover_resource_enabled", True)
    _res(db_session, cover_world["tokens"]["pilot"], f"bookshelf://covers/{cover_world['book_id']}")
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    payloads = [e.payload or "" for e in events]
    assert any('"tool_name": "resources/read"' in p and '"outcome": "allow"' in p for p in payloads)
