"""WBS-1：PUBLIC_BASE_URL 配置与 Host Header 安全测试。

验证：
1. public_base_url 字段校验（拒绝路径、查询串、fragment、凭证）
2. Host Header 注入不能改变发现面响应中的链接
3. 可信代理 allowlist 行为
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings


# ── public_base_url 校验 ──

class TestPublicBaseUrlValidation:

    def test_none_is_allowed(self):
        """未配置 public_base_url 时返回 None。"""
        s = Settings(public_base_url=None)
        assert s.public_base_url is None

    def test_empty_string_treated_as_none(self):
        """空字符串等价于 None。"""
        s = Settings(public_base_url="")
        assert s.public_base_url is None

    def test_valid_http_url(self):
        """合法的 http URL 通过校验。"""
        s = Settings(public_base_url="http://192.168.1.20:8000")
        assert s.public_base_url == "http://192.168.1.20:8000"

    def test_valid_https_url(self):
        """合法的 https URL 通过校验。"""
        s = Settings(public_base_url="https://bookshelf.home")
        assert s.public_base_url == "https://bookshelf.home"

    def test_trailing_slash_stripped(self):
        """尾部斜杠被移除。"""
        s = Settings(public_base_url="https://bookshelf.home/")
        assert s.public_base_url == "https://bookshelf.home"

    def test_rejects_path(self):
        """带路径的 URL 被拒绝。"""
        with pytest.raises(Exception):
            Settings(public_base_url="https://bookshelf.home/api")

    def test_rejects_query_string(self):
        """带查询串的 URL 被拒绝。"""
        with pytest.raises(Exception):
            Settings(public_base_url="https://bookshelf.home?foo=bar")

    def test_rejects_fragment(self):
        """带 fragment 的 URL 被拒绝。"""
        with pytest.raises(Exception):
            Settings(public_base_url="https://bookshelf.home#section")

    def test_rejects_credentials(self):
        """带凭证的 URL 被拒绝。"""
        with pytest.raises(Exception):
            Settings(public_base_url="https://user:pass@bookshelf.home")

    def test_rejects_non_http_scheme(self):
        """非 http/https scheme 被拒绝。"""
        with pytest.raises(Exception):
            Settings(public_base_url="ftp://bookshelf.home")

    def test_rejects_no_scheme(self):
        """无 scheme 的值被拒绝。"""
        with pytest.raises(Exception):
            Settings(public_base_url="bookshelf.home")


# ── Host Header 注入防护 ──

class TestHostHeaderInjection:

    def test_manifest_with_evil_host_header(self, client: TestClient):
        """伪造 Host 头不能改变 Manifest 中的链接。"""
        evil_host = "evil.attacker.com"
        r_normal = client.get("/agent/manifest.json")
        r_evil = client.get("/agent/manifest.json", headers={"Host": evil_host})

        # 两个响应的 links 应该一致（都使用配置的 base_url 或相对路径）
        normal_links = r_normal.json().get("links", {})
        evil_links = r_evil.json().get("links", {})

        # 如果 public_base_url 未配置，链接应为相对路径，不受 Host 影响
        # 如果配置了，链接应使用配置值，不受 Host 影响
        for key in normal_links:
            assert evil_links.get(key) == normal_links.get(key), (
                f"Host Header 注入改变了 {key}: normal={normal_links[key]}, evil={evil_links.get(key)}"
            )
            assert evil_host not in str(evil_links.get(key, "")), (
                f"恶意 Host 出现在 links.{key} 中"
            )

    def test_api_catalog_with_evil_host_header(self, client: TestClient):
        """伪造 Host 头不能改变 API Catalog 中的链接。"""
        evil_host = "evil.attacker.com"
        r = client.get("/.well-known/api-catalog", headers={"Host": evil_host})
        assert r.status_code == 200
        # 恶意 Host 不应出现在任何 href 中
        text = json.dumps(r.json())
        assert evil_host not in text, "恶意 Host 出现在 API Catalog 响应中"

    def test_skills_index_with_evil_host_header(self, client: TestClient):
        """伪造 Host 头不能改变 Skills 索引中的 archive_url。"""
        evil_host = "evil.attacker.com"
        r = client.get("/agent/skills/index.json", headers={"Host": evil_host})
        assert r.status_code == 200
        for skill in r.json().get("skills", []):
            assert evil_host not in skill.get("archive_url", ""), (
                f"恶意 Host 出现在 {skill.get('name')} 的 archive_url 中"
            )

    def test_x_forwarded_host_ignored_without_trusted_proxy(self, client: TestClient):
        """未配置可信代理时 X-Forwarded-Host 被忽略。"""
        evil_host = "evil.attacker.com"
        r = client.get(
            "/agent/manifest.json",
            headers={"X-Forwarded-Host": evil_host, "X-Forwarded-Proto": "https"},
        )
        assert r.status_code == 200
        text = json.dumps(r.json())
        assert evil_host not in text, "X-Forwarded-Host 在未配置可信代理时被错误信任"


# ── CORS 配置 ──

class TestCorsConfig:

    def test_cors_origin_list_default(self):
        """默认 CORS 为 *。"""
        s = Settings()
        assert "*" in s.cors_origin_list

    def test_cors_origin_list_custom(self):
        """自定义 CORS Origin 列表正确解析。"""
        s = Settings(cors_origins="http://localhost:5173,https://bookshelf.home")
        assert "http://localhost:5173" in s.cors_origin_list
        assert "https://bookshelf.home" in s.cors_origin_list

    def test_trusted_proxy_host_list_default_empty(self):
        """未配置可信代理时列表为空。"""
        s = Settings()
        assert s.trusted_proxy_host_list == []

    def test_trusted_proxy_host_list_custom(self):
        """自定义可信代理列表正确解析并转小写。"""
        s = Settings(trusted_proxy_hosts="proxy.local,Reverse.Proxy.COM")
        assert "proxy.local" in s.trusted_proxy_host_list
        assert "reverse.proxy.com" in s.trusted_proxy_host_list
