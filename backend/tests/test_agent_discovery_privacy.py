"""WBS-9：Agent 发现面隐私测试。

验证发现面不泄露任何业务数据。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_manifest_no_business_data(client: TestClient):
    """Manifest 不包含任何业务数据字段。"""
    r = client.get("/agent/manifest.json")
    assert r.status_code == 200
    manifest = r.json()

    # data_policy 应明确声明不含业务数据
    assert manifest["data_policy"]["discovery_contains_business_data"] is False
    assert manifest["data_policy"]["business_access_requires_user_authorization"] is True


def test_public_health_no_sensitive_info(client: TestClient):
    """/public-health 不泄露敏感信息。"""
    r = client.get("/api/v1/public-health")
    assert r.status_code == 200
    body = r.json()
    data = body.get("data", body)
    assert "status" in data
    assert "service" in data
    assert "authorization_required" in data
    # 不应包含数据库连接信息、成员数等
    assert "database" not in data
    assert "google_books_configured" not in data
    assert "members" not in data


def test_bootstrap_md_no_tokens(client: TestClient):
    """Bootstrap Markdown 不包含任何 token 或密钥。"""
    r = client.get("/agent/bootstrap.md")
    assert r.status_code == 200
    text = r.text
    # 不应包含密钥模式
    assert "hbs_at_" not in text or "hbs_at_<" in text  # 只能是占位符
    assert "Bearer <" in text or "Bearer" not in text  # 只能是模板


def test_api_catalog_linkset_format(client: TestClient):
    """API Catalog 符合 RFC 9727 linkset+json 格式。"""
    r = client.get("/.well-known/api-catalog")
    assert r.status_code == 200
    assert "application/linkset+json" in r.headers.get("content-type", "")
    data = r.json()
    # linkset 格式：顶层有 linkset 数组
    assert "linkset" in data
    assert isinstance(data["linkset"], list)
    assert len(data["linkset"]) > 0


def test_agent_openapi_no_management_endpoints(client: TestClient):
    """Agent OpenAPI 不包含管理端点。"""
    r = client.get("/agent/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    paths = spec.get("paths", {})

    # 不应包含 agent-access 管理端点
    for path in paths:
        assert "agent-access" not in path, f"管理端点泄露: {path}"
        assert "/auth/" not in path, f"认证管理端点泄露: {path}"


def test_skills_index_no_secrets(client: TestClient):
    """Skills 索引不包含密钥。"""
    r = client.get("/agent/skills/index.json")
    assert r.status_code == 200
    data = r.json()
    assert "skills" in data
    for skill in data["skills"]:
        # 每个 skill 暴露 name, description, requested_scopes, version, archive_url 等
        assert "name" in skill
        assert "description" in skill
        assert "requested_scopes" in skill
        # 不应包含文件路径、密钥等
        assert "file_path" not in skill
        assert "secret" not in skill
        assert "token" not in str(skill).lower() or skill["name"] == "bookshelf-bootstrap"


def test_anonymous_cannot_access_business_endpoints(client: TestClient):
    """匿名请求不能写入业务端点（在有渠道绑定的情况下）。

    注意：GET 端点目前仍允许匿名访问（一期设计），
    WBS-6 的完全迁移将逐步收紧读取端点。
    这里只验证写端点的保护。
    """
    # 先创建成员和绑定
    m = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert m.status_code == 201
    mid = m.json()["data"]["id"]
    client.post(
        "/api/v1/members/bind",
        json={"member_id": mid, "channel": "feishu", "external_user_id": "ou_test"},
    )

    # 匿名创建书籍应被拒绝
    r = client.post("/api/v1/books", json={"title": "test"})
    assert r.status_code in (401, 403), f"匿名不应能写入业务端点: {r.status_code}"

    # X-UI-Client: web 旁路也不应工作（WBS-6 移除了旁路）
    r = client.post("/api/v1/books", json={"title": "test"}, headers={"X-UI-Client": "web"})
    assert r.status_code in (401, 403), f"X-UI-Client 旁路不应有效: {r.status_code}"

    # 匿名创建成员应被拒绝
    r = client.post("/api/v1/members", json={"name": "hacker", "role": "member"})
    assert r.status_code in (401, 403)


def test_discovery_endpoints_anonymous_accessible(client: TestClient):
    """发现面端点匿名可访问。"""
    endpoints = [
        "/agent/manifest.json",
        "/agent/bootstrap.md",
        "/.well-known/api-catalog",
        "/llms.txt",
        "/agent/openapi.json",
        "/agent/skills/index.json",
        "/api/v1/public-health",
    ]
    for ep in endpoints:
        r = client.get(ep)
        assert r.status_code == 200, f"发现面端点 {ep} 应匿名可访问: {r.status_code}"
