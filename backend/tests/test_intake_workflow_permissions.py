"""PLN-012 M3 权限与端到端（BI-16/17 后端）。

矩阵（规划 §4.8）：
- 匿名：全部 401；候选图片读取 401（不复用匿名封面入口）；
- Member（非 Owner）：工作台读写一律 403——工作台不随 books:write 开放；
- Owner：完整链路（建任务→传图→候选→预览→确认→执行→快照），刷新（重新
  GET）可恢复状态；重复点击执行不重复建书；CSRF 缺 Origin 被拒。
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, Member, IntakeCommandExecution
from app.services import agent_access

import io

from PIL import Image


def _png_bytes(color=(1, 2, 3)) -> bytes:
    """真实可解码 PNG（假字节会被契约判定为图片损坏而如实失败）。"""
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buf, format="PNG")
    return buf.getvalue()


def _member_client(db_session: Session) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=("127.0.0.1", 50000))


# ── 权限矩阵 ──

def test_anonymous_rejected_everywhere(anon_client: TestClient):
    base = "/api/v1/intake-workflow"
    assert anon_client.get(f"{base}/work-items").status_code == 401
    assert anon_client.post(f"{base}/work-items", json={"title": "x"}).status_code == 401
    assert anon_client.get(f"{base}/work-items/1").status_code == 401
    assert anon_client.post(f"{base}/work-items/1/confirm", json={"candidate_ids": []}).status_code == 401
    assert anon_client.post(f"{base}/work-items/1/execute").status_code == 401
    assert anon_client.get(f"{base}/photos/1/image").status_code == 401


def test_member_rejected_for_workbench(db_session: Session):
    member = Member(name="工作台成员", role="member")
    db_session.add(member)
    db_session.commit()
    agent_access.set_member_password(db_session, member, "member-pass-12345")
    c = _member_client(db_session)
    login = c.post("/auth/login", json={"username": member.username, "password": "member-pass-12345"})
    assert login.status_code == 200, login.text
    base = "/api/v1/intake-workflow"
    assert c.get(f"{base}/work-items").status_code == 403
    assert c.post(f"{base}/work-items", json={"title": "x"},
                  headers={"Origin": "http://127.0.0.1"}).status_code == 403
    assert c.post(f"{base}/work-items/1/execute",
                  headers={"Origin": "http://127.0.0.1"}).status_code == 403


def test_owner_csrf_required_for_writes(client: TestClient, db_session: Session):
    # client 夹具默认带 Origin；构造不带 Origin 的同会话客户端验证 CSRF
    token = client.cookies.get("hbs_session")
    raw = TestClient(app)
    raw.cookies.set("hbs_session", token, domain="testserver.local")
    r = raw.post("/api/v1/intake-workflow/work-items", json={"title": "x"})
    assert r.status_code == 403


# ── Owner 完整链路（BI-16/17）──

def _owner_flow(client: TestClient, db_session: Session) -> dict:
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "客厅书架批次"},
                    headers=headers)
    assert r.status_code == 200, r.text
    item_id = r.json()["data"]["id"]

    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                    files=[("files", ("a.png", _png_bytes(), "image/png")),
                           ("files", ("b.png", _png_bytes(), "image/png"))],
                    data={"photo_ids": "p0001,p0002"}, headers=headers)
    assert r.status_code == 200, r.text
    added = r.json()["data"]["added"]
    assert [p["photo_id"] for p in added] == ["p0001", "p0002"]

    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/candidates",
                    json={"recognized": {
                        "p0001": {"title": "活着", "authors": ["余华"], "confidence": 0.9},
                        "p0002": {"title": "活着", "authors": ["余华"], "confidence": 0.8},
                    }}, headers=headers)
    assert r.status_code == 200, r.text
    candidates = r.json()["data"]["candidates"]
    assert len(candidates) == 1  # 相同内容照片同组
    cand_id = candidates[0]["id"]

    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/preview-matches",
                    headers=headers)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": [cand_id]}, headers=headers)
    assert r.status_code == 200, r.text
    exec_id = r.json()["data"]["executions"][0]["id"]

    with patch("app.services.intake.fetch_metadata", return_value=None):
        r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/execute", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "completed"

    # 刷新可恢复：重新 GET 拿到完整状态（含回执与 book_id）
    r = client.get(f"/api/v1/intake-workflow/work-items/{item_id}")
    assert r.status_code == 200
    snapshot = r.json()["data"]
    assert snapshot["status"] == "completed"
    assert snapshot["candidates"][0]["status"] == "executed"
    return {"item_id": item_id, "cand_id": cand_id, "exec_id": exec_id,
            "book_id": snapshot["executions"][0]["book_id"]}


def test_owner_full_flow_and_duplicate_execute_clicks(client: TestClient, db_session: Session):
    ctx = _owner_flow(client, db_session)
    headers = {"Origin": "http://127.0.0.1"}
    books_before = db_session.scalar(select(func.count()).select_from(Book))

    # 重复点击执行（再点两次）：幂等，不重复建书/回执
    for _ in range(2):
        with patch("app.services.intake.fetch_metadata", return_value=None):
            r = client.post(
                f"/api/v1/intake-workflow/work-items/{ctx['item_id']}/execute", headers=headers)
        assert r.status_code == 200
        assert r.json()["data"]["status"] == "completed"

    assert db_session.scalar(select(func.count()).select_from(Book)) == books_before
    executions = db_session.scalars(select(IntakeCommandExecution)).fetchall()
    assert len(executions) == 1 and executions[0].book_id == ctx["book_id"]


def test_photo_image_requires_auth_but_readable_by_session(client: TestClient, db_session: Session):
    ctx = _owner_flow(client, db_session)
    # Owner 会话可读候选图片
    r = client.get("/api/v1/intake-workflow/photos/1/image")
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_edit_candidate_bumps_version_via_api(client: TestClient, db_session: Session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"}, headers=headers)
    item_id = r.json()["data"]["id"]
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/photos",
                files=[("files", ("a.jpg", b"img", "image/jpeg"))],
                data={"photo_ids": "p0001"}, headers=headers)
    client.post(f"/api/v1/intake-workflow/work-items/{item_id}/candidates",
                json={"recognized": {"p0001": {"title": "三体"}}}, headers=headers)
    cand_id = client.get(f"/api/v1/intake-workflow/work-items/{item_id}").json()[
        "data"]["candidates"][0]["id"]

    r = client.patch(f"/api/v1/intake-workflow/candidates/{cand_id}",
                     json={"title": "三体（纪念版）"}, headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["version"] == 2 and data["title"] == "三体（纪念版）"


def test_confirm_missing_candidate_returns_400(client: TestClient, db_session: Session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"}, headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/confirm",
                    json={"candidate_ids": [9999]}, headers=headers)
    assert r.status_code == 400
    assert "不属于本任务" in r.text


def test_execute_without_confirmation_returns_400(client: TestClient, db_session: Session):
    headers = {"Origin": "http://127.0.0.1"}
    r = client.post("/api/v1/intake-workflow/work-items", json={"title": "t"}, headers=headers)
    item_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/intake-workflow/work-items/{item_id}/execute", headers=headers)
    assert r.status_code == 400
    assert "没有可执行命令" in r.text
