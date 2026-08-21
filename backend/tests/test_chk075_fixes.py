"""CHK-075 回归测试：权限阶段 2 未提交代码审查发现的三处缺陷。

- BUG-202：/files/attachments 归属门禁可被路径混淆绕过（./、/../、/. 等变体
  使 DB 查库 miss，落入"无归属=家庭共享"分支）；修复为归一化后查库。
  验证必须走原始 ASGI 请求：TestClient(httpx) 会在客户端侧规范化路径，
  无法复现 raw HTTP（curl --path-as-is / 原始 socket）发送的畸形路径。
- BUG-203：停用成员的 Agent Token 与渠道头身份不失效（仅 Web 会话被撤销）；
  修复为 verify_token / 渠道解析处 fail-closed。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Attachment, Book, Member, ReadingNote
from app.services import agent_access
from app.utils.book_helpers import serialize_json_list


def _make_web_client(db_session: Session) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    c = TestClient(app)
    c.headers.update({"Origin": "http://127.0.0.1"})
    return c


async def _raw_asgi_get(path: str, cookies: dict) -> tuple[int, bytes]:
    """构造未规范化的原始 HTTP 路径，直接过 ASGI 栈（等价 uvicorn 收到的请求行）。"""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": [(b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode())],
        "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
    }
    received = {"status": 0, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            received["status"] = message["status"]
        elif message["type"] == "http.response.body":
            received["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return received["status"], received["body"]


def _setup_private_attachment(client: TestClient, db_session: Session) -> dict:
    """成员乙的私有笔记附件 + 成员丙登录态，返回场景句柄。"""
    r = client.post("/api/v1/members", json={"name": "乙-CHK075", "role": "member"})
    member_b = r.json()["data"]["id"]
    client.post(f"/api/v1/members/{member_b}/password", json={"password": "member-b-pass-123"})

    book = Book(title="CHK075书", authors=serialize_json_list(["作者"]))
    db_session.add(book)
    db_session.commit()
    note = ReadingNote(book_id=book.id, member_id=member_b, content_md="乙的笔记")
    db_session.add(note)
    db_session.commit()

    attachments_dir = Path(settings.attachments_dir)
    attachments_dir.mkdir(parents=True, exist_ok=True)
    (attachments_dir / "chk075_private.txt").write_text("B_PRIVATE_SENTINEL")
    db_session.add(Attachment(entity_type="note", entity_id=note.id, attach_type="file",
                              title="乙笔记附件", file_path="attachments/chk075_private.txt"))
    db_session.commit()

    r = client.post("/api/v1/members", json={"name": "丙-CHK075", "role": "member"})
    member_c = r.json()["data"]["id"]
    r = client.post(f"/api/v1/members/{member_c}/password", json={"password": "member-c-pass-123"})
    username_c = r.json()["data"]["username"]
    cc = _make_web_client(db_session)
    assert cc.post("/auth/login", json={"username": username_c, "password": "member-c-pass-123"}).status_code == 200
    return {"member_b": member_b, "member_c": member_c, "client_c": cc,
            "token_c": cc.cookies.get("hbs_session")}


def test_attachment_path_confusion_blocked(client: TestClient, db_session: Session) -> None:
    """BUG-202：./、/../、/. 变体不得绕过私有附件门禁（raw HTTP 口径）。"""
    world = _setup_private_attachment(client, db_session)
    token = world["token_c"]
    base = "/api/v1/files/attachments"

    # 基线：直接路径 404（防枚举）；归属人乙与 Owner 仍可访问
    assert client.get(f"{base}/chk075_private.txt").status_code == 200  # owner fixture 会话
    status, _ = asyncio.run(_raw_asgi_get(f"{base}/chk075_private.txt", {"hbs_session": token}))
    assert status == 404

    for variant in ["./chk075_private.txt", "x/../chk075_private.txt",
                    "chk075_private.txt/.", "chk075_private.txt/."]:
        status, body = asyncio.run(_raw_asgi_get(f"{base}/{variant}", {"hbs_session": token}))
        assert status == 404, f"变体 {variant} 绕过门禁"
        assert b"B_PRIVATE_SENTINEL" not in body


def test_disabled_member_agent_token_rejected(client: TestClient, db_session: Session) -> None:
    """BUG-203：停用成员的 Agent Token 即时失效，恢复后可用。"""
    r = client.post("/api/v1/members", json={"name": "丁-CHK075", "role": "member"})
    member_d = r.json()["data"]["id"]
    owner_id = client.get("/auth/session").json()["member_id"]
    ac = agent_access.register_agent_client(db_session, display_name="chk075-agent")
    grant = agent_access.create_grant(
        db_session, agent_client_id=ac.id, member_id=member_d, scopes=["books:read"],
        approved_by_member_id=owner_id,
    )
    token_plain, _ = agent_access.issue_token(db_session, grant.id)
    assert agent_access.verify_token(db_session, token_plain) is not None

    client.patch(f"/api/v1/members/{member_d}", json={"disabled": True})
    db_session.expire_all()
    assert agent_access.verify_token(db_session, token_plain) is None

    client.patch(f"/api/v1/members/{member_d}", json={"disabled": False})
    db_session.expire_all()
    assert agent_access.verify_token(db_session, token_plain) is not None


def test_disabled_member_channel_rejected(client: TestClient, db_session: Session) -> None:
    """BUG-203：停用成员的渠道头身份即时失效。"""
    r = client.post("/api/v1/members", json={"name": "戊-CHK075", "role": "member"})
    member_e = r.json()["data"]["id"]
    client.post("/api/v1/members/bind", json={
        "member_id": member_e, "channel": "feishu", "external_user_id": "ou_chk075_e",
    })
    headers = {"X-Channel": "feishu", "X-External-User-Id": "ou_chk075_e"}
    assert client.get("/api/v1/books", headers=headers).status_code == 200

    client.patch(f"/api/v1/members/{member_e}", json={"disabled": True})
    r = client.get("/api/v1/books", headers=headers)
    assert r.status_code == 403
    assert "停用" in r.text


def test_mcp_tool_malformed_params_structured_error(db_session: Session) -> None:
    """BUG-204：MCP 工具畸形 JSON 类型不得 500，无效枚举不得静默忽略。"""
    from app.mcp_server.tools import catalog

    # 非字符串筛选值 -> 结构化 PARAM_INVALID（此前 AttributeError -> 500）
    for args in ({"query": 123}, {"query": True}, {"query": ["a"]}, {"author": 42},
                 {"category": 3.14}, {"language": {"k": "v"}}):
        with pytest.raises(catalog.ToolError) as ei:
            catalog.search_books(db_session, args)
        assert ei.value.code == "PARAM_INVALID"

    # 非字符串 cursor -> 结构化 INVALID_CURSOR（此前 AttributeError -> 500）
    for args in ({"query": "x", "cursor": 123}, {"query": "x", "cursor": True}):
        with pytest.raises(catalog.ToolError) as ei:
            catalog.search_books(db_session, args)
        assert ei.value.code == "INVALID_CURSOR"

    # 无效 availability 枚举 -> PARAM_INVALID（此前静默当作不过滤）
    for args in ({"availability": "garbage"}, {"availability": 5}):
        with pytest.raises(catalog.ToolError) as ei:
            catalog.search_books(db_session, args)
        assert ei.value.code == "PARAM_INVALID"

    # 合法枚举仍然可用（不因收紧校验误伤）
    r = catalog.search_books(db_session, {"availability": "in_shelf"})
    assert isinstance(r["count"], int)
