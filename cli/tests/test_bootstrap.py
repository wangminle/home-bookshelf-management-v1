"""WBS-8：CLI bootstrap 命令测试。

验证 Agent 在无业务权限时能完成发现与兼容性检查。
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bookshelf.main import app

runner = CliRunner()


def _mock_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "service": {
            "id": "home-bookshelf",
            "name": "家庭图书管理系统",
            "version": "0.2.5",
            "description": "自托管家庭藏书管理服务",
        },
        "links": {
            "human_entry": "/agent",
            "agent_guide": "/agent/bootstrap.md",
            "api_catalog": "/.well-known/api-catalog",
            "openapi": "/agent/openapi.json",
            "skills_index": "/agent/skills/index.json",
        },
        "data_policy": {
            "discovery_contains_business_data": False,
            "business_access_requires_user_authorization": True,
        },
        "capabilities": [
            {
                "id": "books.search",
                "description": "搜索藏书",
                "authorization_required": True,
                "required_scopes": ["books:read"],
                "risk": "read",
            },
        ],
        "skills": {
            "bundle_version": "0.2.5",
            "index": "/agent/skills/index.json",
        },
    }


def _mock_skills_index() -> dict:
    return {
        "bundle_version": "0.2.5",
        "skills": [
            {
                "name": "book-intake",
                "version": "0.2.5",
                "description": "图书入库",
                "archive_url": "/agent/skills/download/0.2.5.zip",
                "sha256": "abc123",
                "size_bytes": 16000,
                "requested_scopes": ["books:write"],
                "has_scripts": False,
                "has_network_access": True,
                "writes_data": True,
            },
        ],
    }


def _mock_health() -> dict:
    return {
        "ok": True,
        "data": {
            "status": "available",
            "service": "home-bookshelf",
            "authorization_required": True,
        },
        "error": None,
    }


class _MockResponse:
    def __init__(self, status_code: int, json_data: dict | None = None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class TestBootstrapCommand:
    """bookshelf bootstrap <url> — 发现系统契约。"""

    def test_bootstrap_success_json(self):
        """成功获取 manifest + skills + health，JSON 输出。"""
        def mock_get(url, **kwargs):
            if "manifest.json" in url:
                return _MockResponse(200, _mock_manifest())
            if "skills/index.json" in url:
                return _MockResponse(200, _mock_skills_index())
            if "public-health" in url:
                return _MockResponse(200, _mock_health())
            return _MockResponse(404)

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["bootstrap", "http://127.0.0.1:8000", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["ok"] is True
        assert output["manifest"]["service"]["name"] == "家庭图书管理系统"
        assert output["skills_index"]["bundle_version"] == "0.2.5"
        assert output["health"]["data"]["status"] == "available"

    def test_bootstrap_success_human(self):
        """成功获取，非 JSON 输出。"""
        def mock_get(url, ** kwargs):
            if "manifest.json" in url:
                return _MockResponse(200, _mock_manifest())
            if "skills/index.json" in url:
                return _MockResponse(200, _mock_skills_index())
            if "public-health" in url:
                return _MockResponse(200, _mock_health())
            return _MockResponse(404)

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["bootstrap", "http://127.0.0.1:8000", "--no-json"])

        assert result.exit_code == 0
        assert "✅" in result.output
        assert "家庭图书管理系统" in result.output

    def test_bootstrap_connection_failure(self):
        """连接失败返回 exit_code=1。"""
        import httpx

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["bootstrap", "http://192.168.1.99:8000", "--json"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["ok"] is False
        assert "error" in output

    def test_bootstrap_manifest_error_but_skills_ok(self):
        """manifest 返回 404 但 skills 正常，ok=False 但 skills_index 有值。"""
        def mock_get(url, **kwargs):
            if "manifest.json" in url:
                return _MockResponse(404)
            if "skills/index.json" in url:
                return _MockResponse(200, _mock_skills_index())
            if "public-health" in url:
                return _MockResponse(200, _mock_health())
            return _MockResponse(404)

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["bootstrap", "http://127.0.0.1:8000", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["ok"] is False
        assert "manifest_error" in output
        assert "skills_index" in output

    def test_bootstrap_no_auth_headers_sent(self):
        """bootstrap 命令不发送任何认证头。"""
        captured_headers: list[dict] = []

        def mock_get(url, headers=None, **kwargs):
            if headers:
                captured_headers.append(headers)
            if "manifest.json" in url:
                return _MockResponse(200, _mock_manifest())
            if "skills/index.json" in url:
                return _MockResponse(200, _mock_skills_index())
            if "public-health" in url:
                return _MockResponse(200, _mock_health())
            return _MockResponse(404)

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=mock_get)
            mock_client_cls.return_value = mock_client

            runner.invoke(app, ["bootstrap", "http://127.0.0.1:8000", "--json"])

        for hdrs in captured_headers:
            assert "Authorization" not in hdrs
            assert "X-Setup-Token" not in hdrs
            assert "X-Channel" not in hdrs


class TestAuthStatusCommand:
    """bookshelf auth status — 检查 Agent 授权状态。"""

    def test_auth_status_no_token(self, monkeypatch):
        """未设置 BOOKSHELF_TOKEN 时返回 exit_code=1。"""
        monkeypatch.delenv("BOOKSHELF_TOKEN", raising=False)
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["has_token"] is False
        assert output["status"] == "unauthorized"

    def test_auth_status_valid_token(self, monkeypatch):
        """有效 Token 返回 authorized + scopes。"""
        monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_test123_secret")
        monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

        introspect_response = {
            "active": True,
            "client_name": "TestAgent",
            "member_name": "owner",
            "scopes": ["books:read", "books:write"],
        }

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(
                return_value=_MockResponse(200, introspect_response)
            )
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["auth", "status", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "authorized"
        assert output["client_name"] == "TestAgent"
        assert "books:read" in output["scopes"]

    def test_auth_status_invalid_token(self, monkeypatch):
        """无效 Token 返回 exit_code=1。"""
        monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_invalid_token")
        monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(return_value=_MockResponse(401))
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["auth", "status", "--json"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "invalid_token"

    def test_auth_status_does_not_print_token(self, monkeypatch):
        """auth status 输出中不出现 Token 明文。"""
        token = "hbs_at_secret123_nottobedisplayed"
        monkeypatch.setenv("BOOKSHELF_TOKEN", token)
        monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(return_value=_MockResponse(200, {"active": True, "scopes": []}))
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["auth", "status", "--no-json"])

        assert token not in result.output

    def test_auth_status_connection_error(self, monkeypatch):
        """连接失败返回 exit_code=1。"""
        import httpx

        monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_test123")
        monkeypatch.setenv("BOOKSHELF_API_URL", "http://192.168.1.99:8000")

        with patch("bookshelf.bootstrap.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get = MagicMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            result = runner.invoke(app, ["auth", "status", "--json"])

        assert result.exit_code == 1
        output = json.loads(result.output)
        assert output["status"] == "connection_error"


class TestDoctorAuthorized:
    """bookshelf doctor --authorized — 授权后业务检查。"""

    def test_doctor_authorized_no_token(self, monkeypatch):
        """无 Token 时 --authorized 返回 exit_code=1。"""
        monkeypatch.delenv("BOOKSHELF_TOKEN", raising=False)
        result = runner.invoke(app, ["doctor", "--authorized", "--json"])
        assert result.exit_code == 1
        assert "BOOKSHELF_TOKEN" in result.output
