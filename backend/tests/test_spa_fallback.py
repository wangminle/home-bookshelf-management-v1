"""BUG-106 回归：SPA fallback 路径穿越防护。

static 托管时，/../ 形式的请求不得逃出 _STATIC_DIR 读取 backend 旁路文件
（如 app/config.py、.env.example）。合法静态文件与 SPA 路由仍正常工作。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, _STATIC_DIR


def test_static_dir_exists_in_repo():
    """CI/本地仓库 backend/static 存在时才挂载 SPA fallback。"""
    # 仅当 static 目录存在时这些断言有意义
    if not _STATIC_DIR.is_dir():
        import pytest
        pytest.skip("backend/static 不存在，SPA fallback 未挂载")
    assert (_STATIC_DIR / "index.html").is_file()


def test_path_traversal_blocked_returns_spa(client: TestClient):
    """BUG-106: /../app/config.py 必须返回 SPA index.html，而非源码。"""
    if not _STATIC_DIR.is_dir():
        import pytest
        pytest.skip("backend/static 不存在")
    r = client.get("/../app/config.py", follow_redirects=False)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text
    assert "from app.config" not in body
    assert "<!DOCTYPE html>" in body or '<div id="app">' in body


def test_url_encoded_traversal_blocked(client: TestClient):
    """BUG-106: 编码穿越 /..%2f 同样必须被拦截。"""
    if not _STATIC_DIR.is_dir():
        import pytest
        pytest.skip("backend/static 不存在")
    r = client.get("/..%2fapp%2fconfig.py", follow_redirects=False)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "from app.config" not in r.text
    assert "<!DOCTYPE html>" in r.text or '<div id="app">' in r.text


def test_unmatched_api_path_returns_json_404(client: TestClient):
    """未注册的 /api/* 不得回落成 SPA HTML 200。"""
    if not _STATIC_DIR.is_dir():
        import pytest
        pytest.skip("backend/static 不存在")
    r = client.get("/api/v1/does-not-exist-spa-guard", follow_redirects=False)
    assert r.status_code == 404
    assert "application/json" in r.headers.get("content-type", "")
    assert "text/html" not in r.headers.get("content-type", "")


def test_legitimate_spa_route_returns_index(client: TestClient):
    """正常 SPA 路由 /books/1 应回退到 index.html。"""
    if not _STATIC_DIR.is_dir():
        import pytest
        pytest.skip("backend/static 不存在")
    r = client.get("/books/1", follow_redirects=False)
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "<!DOCTYPE html>" in r.text or '<div id="app">' in r.text


def test_legitimate_static_file_served(client: TestClient):
    """static 目录内真实文件（如 index.html）应被直接返回。"""
    if not _STATIC_DIR.is_dir():
        import pytest
        pytest.skip("backend/static 不存在")
    r = client.get("/index.html", follow_redirects=False)
    assert r.status_code == 200
    assert "<!DOCTYPE html>" in r.text or '<div id="app">' in r.text
