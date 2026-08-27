"""官方 MCP SDK v2.1.0 客户端兼容性测试（WBS-MCP-P0 关键交付物）。

用 SDK 推荐的 create_mcp_http_client + streamable_http_client 连接我们的 /mcp
端点，验证 discover → list_tools → call_tool 全链路兼容性。

用法: cd backend && python3 tests/mcp/run_sdk_compat.py
"""
import asyncio
import os
import sys
import tempfile
import threading
import time

# 设置独立环境
_db = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_db}/sdk.db",
    "DATA_DIR": f"{_db}/data",
    "SETUP_TOKEN": "sdk-token",
    "MCP_ENABLED": "true",
    "MCP_CURSOR_SIGNING_SECRET": "sdk-0123456789abcdef0123456789abcdef01234",
    "MCP_ALLOWED_HOSTS": "127.0.0.1,localhost",
    "MCP_TRUSTED_CIDRS": "127.0.0.0/8",
    "MCP_REQUIRE_HTTPS": "false",
    "ANONYMOUS_CATALOG_MODE": "disabled",
})

PORT = 9930
BASE = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def setup():
    """迁移 + 启动服务器 + 造测试数据。"""
    from alembic import command
    from alembic.config import Config

    backend = os.path.join(os.path.dirname(__file__), "..", "..")
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    print("✓ alembic 迁移完成")

    import uvicorn
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2)
    print(f"✓ uvicorn 启动 (:{PORT})")

    import httpx
    with httpx.Client(base_url=BASE) as c:
        c.post("/api/v1/members", json={"name": "SDK", "role": "owner"})
        c.post("/auth/init-password", headers={"X-Setup-Token": "sdk-token"},
               json={"password": "sdk-pass-12345", "confirm": "sdk-pass-12345"})
        c.post("/auth/login", json={"password": "sdk-pass-12345"})
        h = {"Cookie": f"hbs_session={c.cookies.get('hbs_session')}", "Origin": BASE}
        r = c.post("/agent-access/clients", headers=h, json={"display_name": "SDK-Agent"})
        cid = r.json()["id"]
        r = c.post("/agent-access/grants", headers=h,
                   json={"agent_client_id": cid, "member_id": 1,
                         "scopes": ["books:read"], "data_scope": "household_shared"})
        r = c.post("/agent-access/tokens", headers=h, json={"grant_id": r.json()["id"]})
        c.post("/api/v1/books", headers=h, json={"title": "SDK兼容书"})
        print("✓ 测试数据就绪")
        return r.json()["token"]


async def run_tests(token: str) -> bool:
    # DOC-046：高层 Client 的首参是 server/传输而非展示名，且其 URL 分支
    # 无法注入 Bearer 头——与 Agent 指引/预检一致，用 ClientSession 显式会话
    from mcp import ClientSession, types
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    results = {"pass": 0, "fail": 0}

    def ok(msg):
        results["pass"] += 1
        print(f"  ✓ {msg}")

    def fail(msg):
        results["fail"] += 1
        print(f"  ✗ {msg}")

    print(f"\n═══ 官方 MCP SDK v2.1.0 客户端兼容性 ═══")
    url = f"{BASE}/mcp"
    http_client = create_mcp_http_client(
        headers={"Authorization": f"Bearer {token}"})

    try:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            ok("streamable_http_client 连接")

            async with ClientSession(read, write) as s:
                ok("ClientSession 初始化")

                # 2026-07-28 握手：server/discover + adopt
                try:
                    disc = await asyncio.wait_for(
                        s.send_request(
                            types.DiscoverRequest(params=types.RequestParams(_meta={})),
                            types.DiscoverResult), timeout=10)
                    s.adopt(disc)
                    ok(f"discover+adopt: {disc.supported_versions}")
                except Exception as e:
                    fail(f"discover+adopt: {type(e).__name__}: {e}")

                # list_tools
                try:
                    tl = await asyncio.wait_for(
                        s.send_request(
                            types.ListToolsRequest(params=types.PaginatedRequestParams(_meta={})),
                            types.ListToolsResult), timeout=10)
                    names = [t.name for t in tl.tools]
                    if "bookshelf_search_books" in names:
                        ok(f"list_tools: {names}")
                    else:
                        fail(f"list_tools 缺核心工具: {names}")
                except Exception as e:
                    fail(f"list_tools: {type(e).__name__}: {e}")

                async def call(name: str, arguments: dict):
                    return await asyncio.wait_for(
                        s.send_request(
                            types.CallToolRequest(params=types.CallToolRequestParams(
                                _meta={}, name=name, arguments=arguments)),
                            types.CallToolResult), timeout=10)

                # search
                try:
                    result = await call("bookshelf_search_books", {"query": "SDK"})
                    if not result.is_error:
                        sc = result.structured_content or {}
                        ok(f"search: count={sc.get('count', '?')}")
                    else:
                        fail(f"search isError")
                except Exception as e:
                    fail(f"search: {type(e).__name__}: {e}")

                # get
                try:
                    result = await call("bookshelf_get_book", {"book_id": 1})
                    if not result.is_error:
                        sc = result.structured_content or {}
                        ok(f"get: title={sc.get('title', '?')}")
                    else:
                        fail("get isError")
                except Exception as e:
                    fail(f"get: {type(e).__name__}: {e}")

    except Exception as e:
        fail(f"连接/初始化: {type(e).__name__}: {e}")

    print(f"\n═══ 结果: PASS={results['pass']} FAIL={results['fail']} ═══")
    return results["fail"] == 0


if __name__ == "__main__":
    token = setup()
    success = asyncio.run(run_tests(token))
    sys.exit(0 if success else 1)
