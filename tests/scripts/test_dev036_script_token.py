"""DEV-036：批量入库脚本的 Token 来源提示须与 CLI 一致（含 Token 文件）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import batch_import_covers as bic  # noqa: E402

sys.path.insert(0, str(PROJECT_ROOT / "tests" / "scripts"))
from test_batch_import_covers import FakeClient, created_resp, entry, make_covers, run_args, write_manifest  # noqa: E402


def _run_once(tmp_path):
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体", author="刘慈欣")])
    fake = FakeClient(behavior={"三体": created_resp(5)})
    rc = bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    return rc, fake


def test_warns_when_no_token_anywhere(tmp_path, capsys):
    rc, _ = _run_once(tmp_path)
    assert rc == 0
    err = capsys.readouterr().err
    assert "未设置 BOOKSHELF_TOKEN" in err and "bookshelf auth login" in err


def test_no_warning_when_env_token_set(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("BOOKSHELF_TOKEN", "t" * 40)
    _run_once(tmp_path)
    assert "未设置" not in capsys.readouterr().err


def test_no_warning_when_token_file_present(tmp_path, capsys, monkeypatch):
    token_file = tmp_path / "tok"
    token_file.write_text("f" * 40 + "\n")
    token_file.chmod(0o600)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(token_file))
    rc, _ = _run_once(tmp_path)
    assert rc == 0
    assert "未设置" not in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="POSIX 文件权限语义：Windows 无 0600 权限位，credentials 按设计跳过该检查")
def test_loose_token_file_aborts_before_any_api_call(tmp_path, capsys, monkeypatch):
    token_file = tmp_path / "tok"
    token_file.write_text("f" * 40 + "\n")
    token_file.chmod(0o644)
    monkeypatch.setenv("BOOKSHELF_TOKEN_FILE", str(token_file))
    covers = make_covers(tmp_path, ["a.jpg"])
    manifest = write_manifest(tmp_path / "m.json", covers, [entry("a.jpg", "confirmed", title="三体", author="刘慈欣")])
    fake = FakeClient(behavior={"三体": created_resp(5)})
    with pytest.raises(SystemExit) as exc:
        bic.cmd_run(run_args(manifest, tmp_path / "r.json"), client=fake)
    assert exc.value.code != 0
    assert fake.calls == []
    err = capsys.readouterr().err
    assert "chmod 600" in err
    assert "f" * 40 not in err
    # 清单未被改写
    assert json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["status"] == "confirmed"
