"""CHK-107 回归：CLI 侧确认 bug 的修复锁定。

覆盖：
- client.health() 对 503+诊断体返回 payload（doctor 不再误诊 API 不可达）
- doctor --authorized JSON 模式输出单一合法 JSON 文档
- 业务错误（RuntimeError）不再以 traceback 抛出，转为一行错误 + exit 1
- collect_auth_status 对 200 非 JSON 响应按错误处理
- bootstrap 对 200 非 JSON 的 manifest 不崩溃
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bookshelf.bootstrap import collect_auth_status
from bookshelf.client import BookshelfClient
from bookshelf.main import app

runner = CliRunner()


class _MockResponse:
    def __init__(self, status_code: int, body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("No JSON")
        return self._body


# --- client.health：503 诊断体原样返回 ----------------------------------------


def test_health_returns_payload_on_503_with_diagnostics():
    """DB 断开的 503 响应带 data.database 时应返回 payload 而非抛错。"""
    client = BookshelfClient(base_url="http://x")
    payload = {
        "ok": False,
        "data": {"status": "degraded", "database": "disconnected"},
        "error": "database disconnected",
    }
    with patch.object(client, "health_probe", return_value=(payload, 503)):
        result = client.health()
    assert result["data"]["database"] == "disconnected"


def test_health_raises_on_503_without_diagnostics():
    """无诊断体的 5xx 仍按连接/服务错误抛 RuntimeError。"""
    import pytest

    client = BookshelfClient(base_url="http://x")
    with patch.object(client, "health_probe", return_value=({"detail": "oops"}, 500)):
        with pytest.raises(RuntimeError):
            client.health()


def test_doctor_reports_db_disconnected_not_unreachable():
    """DB 断开时 doctor 报数据库错误，不再给出'启动 uvicorn'的误诊提示。"""
    from bookshelf.doctor import run_doctor

    mock = MagicMock()
    mock.base_url = "http://127.0.0.1:8000"
    mock.health.return_value = {
        "ok": False,
        "_http_status": 503,
        "data": {"status": "degraded", "database": "disconnected"},
    }
    mock.members.side_effect = RuntimeError("[HTTP 401] unauthorized")
    report = run_doctor(mock)
    assert report.api_reachable is True
    assert report.db_ok is False
    assert any("数据库未连接" in e for e in report.errors)
    assert not any("uvicorn" in h for h in report.hints)


# --- doctor --authorized：单一 JSON 文档 --------------------------------------


def test_doctor_authorized_emits_single_json(monkeypatch):
    """--authorized 成功路径：stdout 为单一可解析 JSON，auth 结果并入文档。"""
    monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_test")
    monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.get = MagicMock(
        return_value=_MockResponse(200, {"active": True, "scopes": ["books:read"]})
    )

    mock_client = MagicMock()
    mock_client.base_url = "http://127.0.0.1:8000"
    mock_client.health.return_value = {
        "ok": True,
        "_http_status": 200,
        "data": {"database": "connected", "app_version": "0.3.15", "frontend_version": "0.3.15"},
    }
    mock_client.members.return_value = {"ok": True, "data": {"items": []}}

    with patch("bookshelf.bootstrap.httpx.Client", return_value=mock_http), \
         patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["doctor", "--authorized", "--json"])

    assert result.exit_code == 0
    doc = json.loads(result.output)  # 两个 JSON 拼接时这里会抛异常
    assert doc["auth_status"]["status"] == "authorized"
    assert doc["ok"] is True


def test_doctor_authorized_invalid_token_single_json(monkeypatch):
    """--authorized 但 Token 无效：单一 JSON + exit 1，不跑 doctor。"""
    monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_invalid")
    monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.get = MagicMock(return_value=_MockResponse(401))

    mock_doctor = MagicMock()
    with patch("bookshelf.bootstrap.httpx.Client", return_value=mock_http), \
         patch("bookshelf.main.run_doctor", mock_doctor):
        result = runner.invoke(app, ["doctor", "--authorized", "--json"])

    assert result.exit_code == 1
    doc = json.loads(result.output)
    assert doc["ok"] is False
    assert doc["auth_status"]["status"] == "invalid_token"
    mock_doctor.assert_not_called()


# --- 业务错误干净退出 ---------------------------------------------------------


def test_cli_runtime_error_outputs_clean_json(monkeypatch):
    """连接失败/HTTP 错误（RuntimeError）输出一行 JSON 错误文档并 exit 1，无 traceback。"""
    monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:59999")

    mock_client = MagicMock()
    mock_client.find.side_effect = RuntimeError("[HTTP 404] 书籍不存在")
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["find", "--keyword", "x", "--json"])

    assert result.exit_code == 1
    doc = json.loads(result.output)
    assert doc == {"ok": False, "error": "[HTTP 404] 书籍不存在"}
    assert "Traceback" not in result.output


def test_cli_runtime_error_text_mode_clean_stderr(monkeypatch):
    monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:59999")

    mock_client = MagicMock()
    mock_client.stats.side_effect = RuntimeError("无法连接 API：http://x")
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["stats", "--no-json"])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "无法连接 API" in result.output


# --- 200 非 JSON 防护 ---------------------------------------------------------


def test_collect_auth_status_non_json_200(monkeypatch):
    """introspect 返回 200 非 JSON（指向错误服务）按 error 处理，不当授权成功。"""
    monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_test")
    monkeypatch.setenv("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.get = MagicMock(return_value=_MockResponse(200, None, text="<html>"))

    with patch("bookshelf.bootstrap.httpx.Client", return_value=mock_http):
        result = collect_auth_status()

    assert result["status"] == "error"
    assert "非 JSON" in result["message"]


def test_bootstrap_non_json_manifest_no_crash():
    """manifest 200 非 JSON 时记入 manifest_error，不抛异常。"""
    from bookshelf.bootstrap import cmd_bootstrap

    mock_http = MagicMock()
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_http.get = MagicMock(return_value=_MockResponse(200, None, text="<html>"))

    with patch("bookshelf.bootstrap.httpx.Client", return_value=mock_http):
        result = runner.invoke(app, ["bootstrap", "http://127.0.0.1:8000", "--json"])

    assert result.exit_code == 0
    doc = json.loads(result.output)
    assert doc["ok"] is False
    assert "非 JSON" in doc["manifest_error"]


# --- --image 展开 ~ ------------------------------------------------------------


def test_image_option_expands_tilde(monkeypatch, tmp_path):
    """--image ~/xxx 展开后校验存在性，给出干净的参数错误而非 typer 原生报错。"""
    from pathlib import Path

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    cover = fake_home / "cover.jpg"
    cover.write_bytes(b"x")
    monkeypatch.setenv("HOME", str(fake_home))

    mock_client = MagicMock()
    mock_client.recognize_isbn.return_value = {"ok": True, "data": {}}
    with patch("bookshelf.main.client", mock_client):
        result = runner.invoke(app, ["recognize", "--image", "~/cover.jpg", "--json"])

    assert result.exit_code == 0
    passed_path = mock_client.recognize_isbn.call_args[0][0]
    assert isinstance(passed_path, Path)
    assert passed_path == cover


def test_image_option_missing_after_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["recognize", "--image", "~/nope.jpg", "--json"])
    assert result.exit_code == 2  # BadParameter：参数错误
    assert "Traceback" not in result.output
