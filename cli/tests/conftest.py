"""CLI 测试隔离：Token 文件解析会读取用户家目录，测试不得触碰真实 ~/.config。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    home = tmp_path / "isolated_home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("BOOKSHELF_TOKEN_FILE", raising=False)
    monkeypatch.delenv("BOOKSHELF_TOKEN", raising=False)
    yield
