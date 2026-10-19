"""DEV-036：Token 文件存储，避免 Token 出现在命令行与进程列表。

优先级：BOOKSHELF_TOKEN 环境变量 > BOOKSHELF_TOKEN_FILE > 默认 ~/.config/bookshelf/token。
文件必须仅属主可读写（0600），否则拒绝读取；Token 明文不得出现在任何输出里。
"""
from __future__ import annotations

import json
import os
import stat
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bookshelf import credentials
from bookshelf.client import BookshelfClient
from bookshelf.main import app

runner = CliRunner()
SECRET = "hbs_at_file_secret_value_123"

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX 文件权限语义")


def _write(path, content: str, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)
    return path


class _Resp:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _patch_introspect(status_code=200, body=None):
    body = body if body is not None else {"active": True, "scopes": ["locations:read"], "client_name": "Agent"}
    http = MagicMock()
    http.__enter__ = MagicMock(return_value=http)
    http.__exit__ = MagicMock(return_value=False)
    http.get = MagicMock(return_value=_Resp(status_code, body))
    return patch("bookshelf.bootstrap.httpx.Client", return_value=http), http


# ── 解析与优先级 ──


def test_default_path_under_home_config(tmp_path):
    assert credentials.token_file_path() == tmp_path / "isolated_home" / ".config" / "bookshelf" / "token"


def test_default_path_honours_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert credentials.token_file_path() == tmp_path / "xdg" / "bookshelf" / "token"


def test_env_token_beats_file(tmp_path, monkeypatch):
    _write(tmp_path / "t", SECRET + "\n")
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(tmp_path / "t"))
    monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_env")
    found = credentials.resolve_token()
    assert (found.token, found.source) == ("hbs_at_env", "env")


def test_token_file_env_is_read_and_stripped(tmp_path, monkeypatch):
    path = _write(tmp_path / "t", f"  {SECRET}\r\n")
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(path))
    found = credentials.resolve_token()
    assert (found.token, found.source, found.path) == (SECRET, "file", path)


def test_default_file_is_used_without_env(tmp_path):
    _write(credentials.token_file_path(), SECRET)
    assert credentials.resolve_token().token == SECRET


def test_no_credentials_returns_none():
    assert credentials.resolve_token() is None


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_group_or_world_accessible_file_is_refused(tmp_path, monkeypatch, mode):
    path = _write(tmp_path / "t", SECRET, mode)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(path))
    with pytest.raises(credentials.TokenFileError) as err:
        credentials.resolve_token()
    assert "chmod 600" in str(err.value)
    assert SECRET not in str(err.value)


def test_explicit_missing_file_is_error_not_silent_fallback(tmp_path, monkeypatch):
    _write(credentials.token_file_path(), "hbs_at_default")
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(credentials.TokenFileError):
        credentials.resolve_token()


@pytest.mark.parametrize("content", ["", "   \n", "two\nlines", "has space"])
def test_empty_or_malformed_file_is_refused(tmp_path, monkeypatch, content):
    path = _write(tmp_path / "t", content)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(path))
    with pytest.raises(credentials.TokenFileError) as err:
        credentials.resolve_token()
    assert "two" not in str(err.value) and "space" not in str(err.value)


def test_directory_as_token_file_is_refused(tmp_path, monkeypatch):
    (tmp_path / "d").mkdir()
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(tmp_path / "d"))
    with pytest.raises(credentials.TokenFileError):
        credentials.resolve_token()


# ── 写入 ──


@posix_only
def test_save_token_creates_private_dir_and_file(tmp_path):
    path = tmp_path / "cfg" / "bookshelf" / "token"
    credentials.save_token(SECRET, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.read_text().strip() == SECRET
    assert [p.name for p in path.parent.iterdir()] == ["token"]


@posix_only
def test_save_token_overwrites_and_keeps_0600(tmp_path):
    path = _write(tmp_path / "token", "old", 0o644)
    credentials.save_token(SECRET, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().strip() == SECRET


def test_save_token_rejects_malformed_token(tmp_path):
    with pytest.raises(credentials.TokenFileError):
        credentials.save_token("two words", tmp_path / "token")
    assert not (tmp_path / "token").exists()


def test_delete_token_is_idempotent(tmp_path):
    path = _write(tmp_path / "token", SECRET)
    assert credentials.delete_token(path) is True
    assert credentials.delete_token(path) is False


# ── client 使用文件 Token ──


def test_client_sends_bearer_from_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(_write(tmp_path / "t", SECRET)))
    assert BookshelfClient(base_url="http://x")._auth_headers() == {"Authorization": f"Bearer {SECRET}"}


def test_client_prefers_env_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(_write(tmp_path / "t", SECRET)))
    monkeypatch.setenv("BOOKSHELF_TOKEN", "hbs_at_env")
    assert BookshelfClient(base_url="http://x")._auth_headers() == {"Authorization": "Bearer hbs_at_env"}


@posix_only
def test_client_refuses_loose_permission_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(_write(tmp_path / "t", SECRET, 0o644)))
    with pytest.raises(RuntimeError) as err:
        BookshelfClient(base_url="http://x")._auth_headers()
    assert SECRET not in str(err.value)


# ── CLI：auth login / logout / status ──


def test_auth_login_stdin_verifies_then_saves(tmp_path, monkeypatch):
    target = tmp_path / "saved" / "token"
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(target))
    patcher, http = _patch_introspect()
    with patcher:
        result = runner.invoke(app, ["auth", "login", "--stdin", "--json"], input=SECRET + "\n")
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    out = json.loads(result.output)
    assert out["ok"] is True and out["saved"] is True and out["path"] == str(target)
    assert "locations:read" in out["scopes"]
    assert target.read_text().strip() == SECRET
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert http.get.call_args.kwargs["headers"] == {"Authorization": f"Bearer {SECRET}"}


def test_auth_login_prompt_is_hidden_and_saved(tmp_path, monkeypatch):
    target = tmp_path / "token"
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(target))
    patcher, _ = _patch_introspect()
    with patcher:
        result = runner.invoke(app, ["auth", "login", "--json"], input=SECRET + "\n")
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    assert target.read_text().strip() == SECRET


def test_auth_login_invalid_token_is_not_saved(tmp_path, monkeypatch):
    target = tmp_path / "token"
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(target))
    patcher, _ = _patch_introspect(401, {})
    with patcher:
        result = runner.invoke(app, ["auth", "login", "--stdin", "--json"], input=SECRET)
    assert result.exit_code == 1
    assert not target.exists()
    assert SECRET not in result.output
    assert json.loads(result.output)["status"] == "invalid_token"


def test_auth_login_no_verify_saves_without_network(tmp_path, monkeypatch):
    target = tmp_path / "token"
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(target))
    with patch("bookshelf.bootstrap.httpx.Client", side_effect=AssertionError("must not call network")):
        result = runner.invoke(app, ["auth", "login", "--stdin", "--no-verify", "--json"], input=SECRET)
    assert result.exit_code == 0, result.output
    assert target.read_text().strip() == SECRET
    assert SECRET not in result.output


def test_auth_login_empty_stdin_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(tmp_path / "token"))
    result = runner.invoke(app, ["auth", "login", "--stdin", "--json"], input="\n")
    assert result.exit_code == 1
    assert not (tmp_path / "token").exists()


def test_auth_login_has_no_token_argument():
    result = runner.invoke(app, ["auth", "login", "--token", SECRET])
    assert result.exit_code != 0
    assert "No such option" in result.output or "no such option" in result.output.lower()


def test_auth_logout_removes_file_and_is_idempotent(tmp_path, monkeypatch):
    target = _write(tmp_path / "token", SECRET)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(target))
    first = runner.invoke(app, ["auth", "logout", "--json"])
    second = runner.invoke(app, ["auth", "logout", "--json"])
    assert first.exit_code == 0 and json.loads(first.output)["removed"] is True
    assert second.exit_code == 0 and json.loads(second.output)["removed"] is False
    assert not target.exists()


def test_auth_status_reports_file_source_without_secret(tmp_path, monkeypatch):
    path = _write(tmp_path / "token", SECRET)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(path))
    patcher, _ = _patch_introspect()
    with patcher:
        result = runner.invoke(app, ["auth", "status", "--json"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["has_token"] is True and out["token_source"] == "file"
    assert out["token_file"] == str(path)
    assert SECRET not in result.output


@posix_only
def test_auth_status_surfaces_permission_problem(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(_write(tmp_path / "token", SECRET, 0o644)))
    result = runner.invoke(app, ["auth", "status", "--json"])
    assert result.exit_code == 1
    out = json.loads(result.output)
    assert out["status"] == "token_file_error"
    assert "chmod 600" in out["message"]
    assert SECRET not in result.output


def test_auth_status_unauthorized_mentions_file_option():
    result = runner.invoke(app, ["auth", "status", "--no-json"])
    assert result.exit_code == 1
    assert "bookshelf auth login" in result.output


def test_doctor_authorized_accepts_file_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(_write(tmp_path / "token", SECRET)))
    patcher, _ = _patch_introspect()
    fake = MagicMock()
    fake.api_reachable = True
    with patcher, patch("bookshelf.main.run_doctor") as run_doctor, patch("bookshelf.main.emit_doctor"):
        run_doctor.return_value.to_payload.return_value = {"ok": True}
        result = runner.invoke(app, ["doctor", "--authorized", "--json"])
    assert result.exit_code == 0, result.output
