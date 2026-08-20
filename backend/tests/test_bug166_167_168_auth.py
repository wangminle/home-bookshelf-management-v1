"""BUG-166/167/168 回归测试：授权矩阵声明的读/识别端点全部接入 AuthContext 鉴权。

- 无凭证访问矩阵内业务端点（含此前的匿名读端点）→ 401
- 有 Token 缺 Scope → 403；有 Token 有 Scope → 成功
- 书籍详情的敏感子资源（进度/购买/笔记）按各自 scope 过滤（BUG-166）
- Cookie 会话的非安全方法须过 CSRF（verify_csrf 接线，BUG-168）
- 非 owner 身份 member_id 不匹配 → 403；owner 可代表家庭成员
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.main import app
from tests.test_agent_access_e2e import _ensure_owner


def _agent_token(client: TestClient, scopes: list[str], member_id: int | None = None) -> tuple[int, str]:
    """在 owner 会话（conftest 夹具已认证）下创建 Agent Client/Grant/Token。

    member_id 缺省绑定 owner 成员；传入其他成员则绑定该成员（非 owner 语义）。
    """
    if member_id is None:
        member_id = _ensure_owner(client)

    r = client.post("/agent-access/clients", json={"display_name": "Auth Bug Test Agent"})
    assert r.status_code == 200, r.text
    agent_client_id = r.json()["id"]

    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id,
        "member_id": member_id,
        "scopes": scopes,
    })
    assert r.status_code == 200, r.text
    grant_id = r.json()["id"]

    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    assert r.status_code == 200, r.text
    return member_id, r.json()["token"]


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


# ── BUG-167/168：无凭证一律 401（含原匿名读端点）──

def test_anonymous_business_endpoints_rejected(anon_client: TestClient):
    cases = [
        ("GET", "/api/v1/books"),
        ("GET", "/api/v1/books/1"),
        ("GET", "/api/v1/stats"),
        ("GET", "/api/v1/members"),
        ("GET", "/api/v1/files/covers/whatever.jpg"),
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/books/intake"),  # 不存在的子路径也不该先于鉴权泄露路由信息
    ]
    for method, path in cases:
        r = anon_client.request(method, path)
        assert r.status_code == 401, f"{method} {path} 匿名应 401，实际 {r.status_code}: {r.text}"

    r = anon_client.post(
        "/api/v1/recognize/isbn",
        files={"image": ("cover.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 401, f"POST /recognize/isbn 匿名应 401，实际 {r.status_code}"

    # 对照：public-health 保持公开（Docker healthcheck 依赖）
    r = anon_client.get("/api/v1/public-health")
    assert r.status_code == 200


def test_health_requires_members_read(client: TestClient):
    member_id, token = _agent_token(client, ["books:read"])
    r = client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, f"缺 members:read 应 403，实际 {r.status_code}: {r.text}"

    member_id, token = _agent_token(client, ["members:read"])
    r = client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["data"]["database"] == "connected"


def test_stats_members_files_scoped(client: TestClient):
    member_id, token = _agent_token(client, ["books:read"])
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/v1/stats", headers=headers)
    assert r.status_code == 403
    r = client.get("/api/v1/members", headers=headers)
    assert r.status_code == 403
    r = client.get("/api/v1/files/covers/none.jpg", headers=headers)
    assert r.status_code == 403

    _, read_all = _agent_token(client, ["stats:read", "members:read", "files:read"])
    headers = {"Authorization": f"Bearer {read_all}"}
    r = client.get("/api/v1/stats", headers=headers)
    assert r.status_code == 200
    r = client.get("/api/v1/members", headers=headers)
    assert r.status_code == 200
    # scope 通过后文件不存在 → 404（而非 401/403）
    r = client.get("/api/v1/files/covers/none.jpg", headers=headers)
    assert r.status_code == 404


# ── BUG-166：详情鉴权 + 敏感子资源 scope 过滤 ──

def _create_book_with_sensitive_data(client: TestClient) -> int:
    r = client.post("/api/v1/books", json={"title": "鉴权测试书"})
    assert r.status_code == 201, r.text
    book_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/books/{book_id}/purchases", json={"price": 42.0})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/books/{book_id}/notes", json={"content_md": "私密笔记"})
    assert r.status_code == 201, r.text
    return book_id


def test_book_detail_filters_sensitive_subresources(client: TestClient):
    book_id = _create_book_with_sensitive_data(client)

    # owner 会话（全 scope）：完整详情
    r = client.get(f"/api/v1/books/{book_id}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["purchase_records"] and data["reading_notes"] and "reading_progress" in data

    # 仅 books:read：详情可见，敏感子资源不下发
    _, token = _agent_token(client, ["books:read"])
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get(f"/api/v1/books/{book_id}", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert "purchase_records" not in data
    assert "reading_notes" not in data
    assert "reading_progress" not in data

    # books:read + purchases:read：购买记录恢复，笔记仍不可见
    _, token2 = _agent_token(client, ["books:read", "purchases:read"])
    r = client.get(f"/api/v1/books/{book_id}", headers={"Authorization": f"Bearer {token2}"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["purchase_records"]
    assert "reading_notes" not in data


# ── BUG-168：verify_csrf 接线 + member 一致性 ──

def test_csrf_rejected_for_cookie_session_without_origin(client: TestClient, db_session):
    from app.services import agent_access

    member_id = _ensure_owner(client)
    session_token, _ = agent_access.create_web_session(db_session, member_id)

    with TestClient(app) as c:  # 无默认 Origin 头
        c.cookies.set("hbs_session", session_token)
        r = c.post("/api/v1/books", json={"title": "CSRF 测试书"})
        assert r.status_code == 403, f"Cookie 会话无 Origin 应被 CSRF 拦截，实际 {r.status_code}"
        assert "CSRF" in r.text or "Origin" in r.text

    # Bearer Token（无 Cookie）不受 CSRF 影响，无需 Origin
    _, token = _agent_token(client, ["books:write"])
    with TestClient(app) as c2:
        r = c2.post(
            "/api/v1/books",
            json={"title": "Bearer 无 Origin 可写"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text


def test_member_id_mismatch_rules(client: TestClient):
    # owner 再建一个普通成员，用于代表操作
    r = client.post("/api/v1/members", json={"name": "家人甲", "role": "member"})
    assert r.status_code == 201, r.text
    family_member_id = r.json()["data"]["id"]

    r = client.post("/api/v1/books", json={"title": "一致性测试书"})
    book_id = r.json()["data"]["id"]

    # owner Web 会话可代表家庭成员更新进度（前端成员切换器语义；首次创建返回 201）
    r = client.post(f"/api/v1/books/{book_id}/progress", json={"member_id": family_member_id, "status": "reading"})
    assert r.status_code in (200, 201), r.text

    # 绑定到普通成员的 Agent Token 指定他人 member_id → 403（矩阵口径：绑定成员）
    _, token = _agent_token(client, ["reading:write"], member_id=family_member_id)
    r = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"member_id": 999999, "status": "reading"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, f"Agent 指定他人 member_id 应 403，实际 {r.status_code}"

    # owner Web 会话代表不存在的成员 → 400（成员存在性校验，BUG-053 语义）
    r = client.post(f"/api/v1/books/{book_id}/progress", json={"member_id": 999999, "status": "reading"})
    assert r.status_code == 400, r.text
