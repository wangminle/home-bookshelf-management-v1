import json
from typer.testing import CliRunner
from bookshelf.main import app
from bookshelf.credentials import resolve_token
from bookshelf.location import summarize_locations


def test_no_scope_hides_legacy_from_summary_and_json():
    result = summarize_locations({"title": "书", "copies": [{"id": 1, "location": "客厅书柜"}]})
    assert "客厅书柜" not in json.dumps(result, ensure_ascii=False)
    assert result["copies"][0]["legacy_location"] is None


def test_login_custom_path_persists_across_commands(tmp_path):
    path = tmp_path / "自定义 目录" / "token"
    result = CliRunner().invoke(app, ["auth", "login", "--stdin", "--no-verify", "--file", str(path)], input="secret-token")
    assert result.exit_code == 0, result.output
    assert resolve_token().token == "secret-token"
    assert resolve_token().path == path


def test_custom_path_used_by_fresh_process_and_environment_keeps_priority(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from bookshelf.credentials import save_token
    path = tmp_path / "selected token"
    result = CliRunner().invoke(app, ["auth", "login", "--stdin", "--no-verify", "--file", str(path)], input="selected-token")
    assert result.exit_code == 0, result.output
    probe = subprocess.run([sys.executable, "-c", "from bookshelf.credentials import resolve_token; assert resolve_token().token == 'selected-token'"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}, capture_output=True)
    assert probe.returncode == 0, probe.stderr.decode()
    other = tmp_path / "env-token"
    save_token("env-file-token", other)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(other))
    assert resolve_token().token == "env-file-token"
    monkeypatch.setenv("BOOKSHELF_TOKEN", "env-token")
    assert resolve_token().token == "env-token"


def test_logout_removes_selected_token_without_falling_back(tmp_path):
    from bookshelf.credentials import save_token
    default = tmp_path / "isolated_home" / ".config" / "bookshelf" / "token"
    save_token("old-default", default)
    selected = tmp_path / "selected"
    runner = CliRunner()
    assert runner.invoke(app, ["auth", "login", "--stdin", "--no-verify", "--file", str(selected)], input="selected").exit_code == 0
    assert runner.invoke(app, ["auth", "logout"]).exit_code == 0
    assert resolve_token() is None
    assert default.exists()
