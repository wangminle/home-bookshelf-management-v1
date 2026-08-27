"""MCP 协议完整验证：用 httpx2 直连（非 SDK transport）验证全部核心行为。

这是 WBS-MCP-P0/P9 的关键交付物——证明我们的 /mcp 服务器协议正确。
SDK v2.1.0 的 StreamableHTTPTransport 存在独立的 URL 构造 bug（UnsupportedProtocol），
不影响我们的服务器协议正确性。

覆盖：discover / tools/list / search / get / 空条件 / 无 Token / 不存在 / 非试点 Grant
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time

_db = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_db}/proto.db",
    "DATA_DIR": f"{_db}/data",
    "SETUP_TOKEN": "proto-token",
    "MCP_ENABLED": "true",
    "MCP_CURSOR_SIGNING_SECRET": "proto-0123456789abcdef0123456789abcdef01",
    "MCP_ALLOWED_HOSTS": "127.0.0.1,localhost",
    "MCP_TRUSTED_CIDRS": "127.0.0.0/8",
    "MCP_REQUIRE_HTTPS": "false",
    "ANONYMOUS_CATALOG_MODE": "disabled",
})

PORT = 9940
BASE = f"http://127.0.0.1:{PORT}"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

RESULTS = {"pass": 0, "fail": 0}


def ok(msg):
    RESULTS["pass"] += 1
    print(f"  ✓ {msg}")


def fail(msg):
    RESULTS["fail"] += 1
    print(f"  ✗ {msg}")


def setup():
    from alembic import command
    from alembic.config import Config

    backend = os.path.join(os.path.dirname(__file__), "..", "..")
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")

    import uvicorn
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(2)

    import httpx
    with httpx.Client(base_url=BASE) as c:
        c.post("/api/v1/members", json={"name": "Proto", "role": "owner"})
        c.post("/auth/init-password", headers={"X-Setup-Token": "proto-token"},
               json={"password": "proto-pass-123", "confirm": "proto-pass-123"})
        c.post("/auth/login", json={"password": "proto-pass-123"})
        h = {"Cookie": f"hbs_session={c.cookies.get('hbs_session')}", "Origin": BASE}
        r = c.post("/agent-access/clients", headers=h, json={"display_name": "Proto-Agent"})
        cid = r.json()["id"]
        # 试点 Grant
        r = c.post("/agent-access/grants", headers=h,
                   json={"agent_client_id": cid, "member_id": 1,
                         "scopes": ["books:read"], "data_scope": "household_shared"})
        r = c.post("/agent-access/tokens", headers=h, json={"grant_id": r.json()["id"]})
        pilot_token = r.json()["token"]
        # 旧语义 Grant（无 data_scope）
        r = c.post("/agent-access/grants", headers=h,
                   json={"agent_client_id": cid, "member_id": 1, "scopes": ["books:read"]})
        r = c.post("/agent-access/tokens", headers=h, json={"grant_id": r.json()["id"]})
        legacy_token = r.json()["token"]

        c.post("/api/v1/books", headers=h, json={"title": "协议验证书"})
        # 造敏感数据
        from app.models import ReadingNote, PurchaseRecord
        from app.db import SessionLocal
        with SessionLocal() as db:
            from app.models import Book
            book = db.query(Book).first()
            db.add(ReadingNote(book_id=book.id, member_id=1, content_md="PROTO_SENTINEL_NOTE"))
            db.add(PurchaseRecord(book_id=book.id, buyer_member_id=1, price=99.9, channel="PROTO_SENTINEL"))
            db.commit()

    return pilot_token, legacy_token


async def run(pilot_token: str, legacy_token: str) -> bool:
    import httpx2

    url = f"{BASE}/mcp"
    base_headers = {"Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28"}

    pilot = httpx2.AsyncClient(headers={**base_headers, "Authorization": f"Bearer {pilot_token}"}, timeout=15)
    legacy = httpx2.AsyncClient(headers={**base_headers, "Authorization": f"Bearer {legacy_token}"}, timeout=15)
    anon = httpx2.AsyncClient(headers=base_headers, timeout=15)

    print("\n═══ MCP 协议完整验证（httpx2 直连）═══")

    async with pilot, legacy, anon:
        # 1. discover
        r = await pilot.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": {}}})
        sv = r.json().get("result", {}).get("supportedVersions", [])
        if r.status_code == 200 and "2026-07-28" in sv:
            ok(f"1. discover: {sv}")
        else:
            fail(f"1. discover: {r.status_code}")

        # 2. tools/list
        r = await pilot.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": {}}})
        tools = [t["name"] for t in r.json().get("result", {}).get("tools", [])]
        if set(tools) == {"bookshelf_search_books", "bookshelf_get_book"}:
            ok(f"2. tools/list: {tools}")
        else:
            fail(f"2. tools/list: {tools}")

        # 3. search
        r = await pilot.post(url, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"_meta": {}, "name": "bookshelf_search_books", "arguments": {"query": "协议"}}})
        sc = r.json().get("result", {}).get("structuredContent", {})
        if not r.json()["result"].get("isError") and sc.get("count", 0) >= 1:
            ok(f"3. search: count={sc['count']}")
        else:
            fail(f"3. search: {json.dumps(r.json()['result'])[:100]}")

        # 4. get
        bid = sc.get("items", [{}])[0].get("id", 1)
        r = await pilot.post(url, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"_meta": {}, "name": "bookshelf_get_book", "arguments": {"book_id": bid}}})
        sc2 = r.json().get("result", {}).get("structuredContent", {})
        if sc2.get("title"):
            ok(f"4. get: {sc2['title']}")
        else:
            fail(f"4. get: {json.dumps(r.json()['result'])[:100]}")

        # 5. 空条件拒绝
        r = await pilot.post(url, json={"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"_meta": {}, "name": "bookshelf_search_books", "arguments": {}}})
        code = r.json()["result"].get("structuredError", {}).get("code")
        if r.json()["result"].get("isError") and code == "QUERY_REQUIRED":
            ok(f"5. 空条件拒绝: {code}")
        else:
            fail(f"5. 空条件: {code}")

        # 6. 无 Token 401
        r = await anon.post(url, json={"jsonrpc": "2.0", "id": 6, "method": "server/discover", "params": {"_meta": {}}})
        if r.status_code == 401:
            ok(f"6. 无 Token 401: {r.json().get('error')}")
        else:
            fail(f"6. 无 Token: {r.status_code}")

        # 7. 不存在 404
        r = await pilot.post(url, json={"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"_meta": {}, "name": "bookshelf_get_book", "arguments": {"book_id": 99999}}})
        err = r.json()["result"].get("structuredError", {})
        if r.json()["result"].get("isError") and err.get("code") == "BOOK_NOT_FOUND":
            ok("7. 不存在 404 防枚举")
        else:
            fail(f"7. 不存在: {err.get('code')}")

        # 8. 非试点 Grant 403
        r = await legacy.post(url, json={"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {"_meta": {}}})
        if r.status_code == 403 and r.headers.get("X-Error-Code") == "PILOT_GRANT_REQUIRED":
            ok(f"8. 非试点 Grant 403: {r.headers.get('X-Error-Code')}")
        else:
            fail(f"8. 非试点: {r.status_code}")

        # 9. 隐私哨兵
        dump = json.dumps(sc) + json.dumps(sc2)
        sentinels_clean = all(s not in dump for s in ["PROTO_SENTINEL_NOTE", "PROTO_SENTINEL", "99.9"])
        if sentinels_clean:
            ok("9. 隐私哨兵零命中")
        else:
            fail("9. 隐私哨兵泄露")

        # 10. structuredContent 存在
        if sc and sc2:
            ok("10. structuredContent 非 None")
        else:
            fail("10. structuredContent 缺失")

    print(f"\n═══ 结果: PASS={RESULTS['pass']} FAIL={RESULTS['fail']} ═══")
    return RESULTS["fail"] == 0


if __name__ == "__main__":
    pilot, legacy = setup()
    success = asyncio.run(run(pilot, legacy))
    sys.exit(0 if success else 1)
