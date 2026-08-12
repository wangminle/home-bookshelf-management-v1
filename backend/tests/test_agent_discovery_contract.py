"""WBS-0：Agent 发现面契约测试。

断言发现响应符合冻结的 Manifest 1.0 Schema，
不出现敏感键名与业务样例。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

# ── 敏感字段黑名单 ──
SENSITIVE_KEYS = {
    "database", "db_url", "database_url", "google_books_api_key",
    "data_dir", "file_path", "members", "member_count", "book_count",
    "bound_channels", "channels", "token", "secret", "password",
    "api_key", "private_key", "connection_string",
}


def _collect_keys(obj, acc: set) -> set:
    """递归收集所有键名。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            _collect_keys(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_keys(item, acc)
    return acc


# ── Manifest 契约 ──

class TestManifestContract:

    def test_manifest_has_required_top_level_fields(self, client: TestClient):
        """Manifest 必须包含 schema_version、service、links、data_policy、capabilities、skills。"""
        r = client.get("/agent/manifest.json")
        assert r.status_code == 200
        m = r.json()
        for key in ("schema_version", "service", "links", "data_policy", "capabilities", "skills"):
            assert key in m, f"Manifest 缺少字段: {key}"

    def test_manifest_schema_version_is_1_0(self, client: TestClient):
        """schema_version 必须为 1.0。"""
        r = client.get("/agent/manifest.json")
        assert r.json()["schema_version"] == "1.0"

    def test_manifest_service_info(self, client: TestClient):
        """service 字段必须包含 id、name、version、description。"""
        r = client.get("/agent/manifest.json")
        svc = r.json()["service"]
        assert svc["id"] == "home-bookshelf"
        assert "name" in svc
        assert "version" in svc
        assert "description" in svc

    def test_manifest_links_all_present(self, client: TestClient):
        """links 字段必须包含所有导航链接。"""
        r = client.get("/agent/manifest.json")
        links = r.json()["links"]
        for key in ("human_entry", "agent_guide", "api_catalog", "openapi", "skills_index", "authorization_manage"):
            assert key in links, f"links 缺少: {key}"
            assert isinstance(links[key], str)
            assert links[key]  # 非空

    def test_manifest_data_policy(self, client: TestClient):
        """data_policy 必须声明不包含业务数据且需要授权。"""
        r = client.get("/agent/manifest.json")
        dp = r.json()["data_policy"]
        assert dp["discovery_contains_business_data"] is False
        assert dp["business_access_requires_user_authorization"] is True
        assert dp["credentials_in_urls"] is False

    def test_manifest_capabilities_have_scopes(self, client: TestClient):
        """每个 capability 必须有 id、description、authorization_required、required_scopes、risk。"""
        r = client.get("/agent/manifest.json")
        caps = r.json()["capabilities"]
        assert len(caps) > 0, "Manifest 必须至少声明一个能力"
        for cap in caps:
            assert "id" in cap
            assert "description" in cap
            assert "authorization_required" in cap
            assert "required_scopes" in cap
            assert "risk" in cap
            assert cap["risk"] in ("read", "write", "delete")
            assert isinstance(cap["required_scopes"], list)

    def test_manifest_skills_ref(self, client: TestClient):
        """skills 字段必须包含 bundle_version 和 index。"""
        r = client.get("/agent/manifest.json")
        skills = r.json()["skills"]
        assert "bundle_version" in skills
        assert "index" in skills
        assert skills["bundle_version"]  # 非空

    def test_manifest_no_sensitive_keys(self, client: TestClient):
        """Manifest 中不得出现任何敏感键名。"""
        r = client.get("/agent/manifest.json")
        keys = _collect_keys(r.json(), set())
        leaked = keys & SENSITIVE_KEYS
        assert not leaked, f"Manifest 泄露敏感键: {leaked}"


# ── API Catalog 契约 ──

class TestApiCatalogContract:

    def test_api_catalog_linkset_format(self, client: TestClient):
        """API Catalog 必须符合 RFC 9727 linkset+json 格式。"""
        r = client.get("/.well-known/api-catalog")
        assert r.status_code == 200
        assert "application/linkset+json" in r.headers.get("content-type", "")
        data = r.json()
        assert "linkset" in data
        assert isinstance(data["linkset"], list)
        assert len(data["linkset"]) > 0

    def test_api_catalog_has_service_desc(self, client: TestClient):
        """linkset 条目必须包含 service-desc。"""
        r = client.get("/.well-known/api-catalog")
        entry = r.json()["linkset"][0]
        assert "anchor" in entry
        assert "service_desc" in entry
        assert len(entry["service_desc"]) > 0
        desc = entry["service_desc"][0]
        assert "href" in desc
        assert "type" in desc

    def test_api_catalog_no_sensitive_keys(self, client: TestClient):
        """API Catalog 不得出现敏感键名。"""
        r = client.get("/.well-known/api-catalog")
        keys = _collect_keys(r.json(), set())
        leaked = keys & SENSITIVE_KEYS
        assert not leaked, f"API Catalog 泄露敏感键: {leaked}"


# ── Skills Index 契约 ──

class TestSkillsIndexContract:

    def test_skills_index_has_bundle_version(self, client: TestClient):
        """Skills 索引必须有 bundle_version 字段。"""
        r = client.get("/agent/skills/index.json")
        assert r.status_code == 200
        data = r.json()
        assert "bundle_version" in data
        assert data["bundle_version"]  # 非空

    def test_skills_index_entries_have_required_fields(self, client: TestClient):
        """每个 skill 条目必须有 name、version、description、archive_url、sha256。"""
        r = client.get("/agent/skills/index.json")
        skills = r.json()["skills"]
        assert len(skills) > 0
        for s in skills:
            assert "name" in s
            assert "version" in s
            assert "description" in s
            assert "archive_url" in s
            assert "sha256" in s
            assert "size_bytes" in s
            assert "requested_scopes" in s

    def test_skills_index_no_sensitive_keys(self, client: TestClient):
        """Skills 索引不得出现敏感键名。"""
        r = client.get("/agent/skills/index.json")
        keys = _collect_keys(r.json(), set())
        leaked = keys & SENSITIVE_KEYS
        assert not leaked, f"Skills 索引泄露敏感键: {leaked}"


# ── Public Health 契约 ──

class TestPublicHealthContract:

    def test_public_health_minimal(self, client: TestClient):
        """公共健康检查只返回最小可用性信息。"""
        r = client.get("/api/v1/public-health")
        assert r.status_code == 200
        data = r.json().get("data", r.json())
        assert "status" in data
        assert "service" in data
        assert "authorization_required" in data

    def test_public_health_no_internals(self, client: TestClient):
        """公共健康检查不泄露内部状态。"""
        r = client.get("/api/v1/public-health")
        keys = _collect_keys(r.json(), set())
        leaked = keys & SENSITIVE_KEYS
        assert not leaked, f"公共健康检查泄露敏感键: {leaked}"
        # 额外检查不包含数据库/第三方配置状态
        data = r.json().get("data", r.json())
        assert "google_books_configured" not in data
        assert "barcode_available" not in data
        assert "database" not in data


# ── Bootstrap Markdown 契约 ──

class TestBootstrapContract:

    def test_bootstrap_md_returns_markdown(self, client: TestClient):
        """Bootstrap 端点返回 Markdown 文本。"""
        r = client.get("/agent/bootstrap.md")
        assert r.status_code == 200
        assert "text/markdown" in r.headers.get("content-type", "")
        assert len(r.text) > 100  # 有实质内容

    def test_bootstrap_md_no_tokens(self, client: TestClient):
        """Bootstrap Markdown 不包含真实 token。"""
        r = client.get("/agent/bootstrap.md")
        text = r.text
        # 只允许占位符形式
        assert "hbs_at_" not in text or "hbs_at_<" in text


# ── Agent OpenAPI 契约 ──

class TestAgentOpenApiContract:

    def test_agent_openapi_no_management_endpoints(self, client: TestClient):
        """Agent OpenAPI 不包含管理端点。"""
        r = client.get("/agent/openapi.json")
        assert r.status_code == 200
        paths = r.json().get("paths", {})
        for path in paths:
            assert "agent-access" not in path, f"管理端点泄露: {path}"
            assert "/auth/" not in path, f"认证管理端点泄露: {path}"

    def test_agent_openapi_has_bearer_scheme(self, client: TestClient):
        """Agent OpenAPI 必须声明 Bearer 安全方案。"""
        r = client.get("/agent/openapi.json")
        spec = r.json()
        schemes = spec.get("components", {}).get("securitySchemes", {})
        assert "BearerAuth" in schemes or any(
            "bearer" in str(v).lower() for v in schemes.values()
        ), "Agent OpenAPI 缺少 Bearer 安全方案"
