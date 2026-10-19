"""批量脚本测试隔离：避免测试读取或修改宿主机 ~/.config/bookshelf 下的真实 Token 文件。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path, monkeypatch):
    home = tmp_path / "isolated_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in ("XDG_CONFIG_HOME", "BOOKSHELF_TOKEN", "BOOKSHELF_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    return home
