#!/usr/bin/env python3
"""MCP 第二期 conformance 评估集（CHK-088/WBS-MCP-9 Task 9.3）：官方 mcp SDK 2.1.0
客户端对自建外壳的实机验证。自动起临时库 uvicorn 实例并执行十题评估 + 标准生命周期
验证，输出 JSON 报告（--report 指定路径）。

覆盖（docs/mcp-client-preflight.md 矩阵对应项）：
  1. 搜索命中 / 2. 详情读取 / 3. 空条件拒绝 / 4. Cursor 翻页 / 5. 不存在书目
  6. 越权 Grant / 7. 撤销 / 8. 限流 / 9. 隐私哨兵 / 10. REST-MCP 一致性
  L1 discover 协商 / L2 tools/list + outputSchema / L3 精确 /mcp 路径 /
  L4 错误协议（-32601/通知静默）/ L5 Mcp-Name 路由头

用法:
  python3 scripts/mcp-sdk-conformance.py [--report FILE] [--port N] [--keep]
  python3 scripts/mcp-sdk-conformance.py --serve [--port N]   # 真实客户端实机连引用

依赖: 官方 mcp==2.1.0（requirements.txt 已锁定）、uvicorn。默认跑完自动清理临时库。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

RESULTS: list[dict] = []


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed(tmp: Path) -> None:
    """空库迁移 + owner/试点 Grant/混合 Grant/令牌/示例书。"""
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{tmp / 'e2e.db'}"
    env["DATA_DIR"] = str(tmp / "data")
    env["SETUP_TOKEN"] = "conformance-setup-token"
    code = f'''
import os, sys
sys.path.insert(0, {str(BACKEND_DIR)!r})
from alembic import command
from alembic.config import Config
from app import db as db_module
from app.models import Book, Member
from app.services import agent_access
cfg = Config({str(BACKEND_DIR / "alembic.ini")!r})
cfg.set_main_option("script_location", {str(BACKEND_DIR / "alembic")!r})
cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
command.upgrade(cfg, "head")
with db_module.SessionLocal() as db:
    owner = Member(name="CF Owner", role="owner", username="cfowner")
    db.add(owner); db.commit()
    for t, a, c in [
        ("三体", '["刘慈欣"]', "科幻"), ("球状闪电", '["刘慈欣"]', "科幻"),
        ("万历十五年", '["黄仁宇"]', "历史"), ("明朝那些事儿", '["当年明月"]', "历史"),
    ]:
        db.add(Book(title=t, authors=a, category=c)); db.commit()
    cid = agent_access.register_agent_client(db, display_name="CF Agent", client_type="codex").id
    grant = agent_access.create_grant(db, agent_client_id=cid, member_id=owner.id,
                                      scopes=["books:read"], data_scope="household_shared",
                                      approved_by_member_id=owner.id)
    tok, _ = agent_access.issue_token(db, grant.id)
    open({str(tmp / "token.txt")!r}, "w").write(tok)
    open({str(tmp / "grant_id.txt")!r}, "w").write(str(grant.id))
    mixed = agent_access.create_grant(db, agent_client_id=cid, member_id=owner.id,
                                      scopes=["books:read", "notes:read"],
                                      data_scope="household_shared",
                                      approved_by_member_id=owner.id)
    mtok, _ = agent_access.issue_token(db, mixed.id)
    open({str(tmp / "mixed_token.txt")!r}, "w").write(mtok)
    print("SEED_OK")
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"seed failed: {r.stderr[-2000:]}")


def _server_env(tmp: Path, port: int) -> dict:
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}",
        "DATA_DIR": str(tmp / "data"),
        "SETUP_TOKEN": "conformance-setup-token",
        "MCP_ENABLED": "true",
        "MCP_CURSOR_SIGNING_SECRET": "conformance-cursor-secret-0123456789abcdef",
        "MCP_ALLOWED_HOSTS": f"127.0.0.1:{port}",
        "MCP_REQUIRE_HTTPS": "false",
        "PYTHONPATH": str(BACKEND_DIR),
    })
    return env


def _start_server(tmp: Path, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=_server_env(tmp, port), stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, cwd=tmp,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/public-health", timeout=1)
            return proc
        except Exception:
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("server failed to start")


def _record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail[:200]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail[:120]}")


def _raw_post(base: str, path: str, payload: dict, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={**(headers or {}), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except Exception as exc:
        try:
            body = exc.read().decode()
        except Exception:
            body = str(exc)
        return getattr(exc, "code", 0), body


async def _evaluate(base: str, token: str, mixed_token: str, tmp: Path) -> None:
    from mcp import ClientSession, types
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    auth_headers = {"Authorization": f"Bearer {token}"}
    proto_headers = {**auth_headers, "MCP-Protocol-Version": "2026-07-28"}

    async def _session(tok: str):
        http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {tok}"})
        ctx = streamable_http_client(f"{base}/mcp", http_client=http_client)
        read, write = await ctx.__aenter__()
        session = ClientSession(read, write)
        try:
            await session.__aenter__()
            disc = await session.send_request(
                types.DiscoverRequest(params=types.RequestParams(_meta={})), types.DiscoverResult)
            session.adopt(disc)
        except BaseException:
            # 同任务内对称退出（撤销/越权用例握手被拒时到达）：
            # 放任 ctx 泄漏会让 GC 在别的任务里 athrow 清理异步生成器，
            # 触发 anyio「Attempted to exit cancel scope in a different task」
            await _close(ctx, session)
            raise
        return ctx, session

    async def _close(ctx, session):
        for closer in (session, ctx):
            try:
                await closer.__aexit__(None, None, None)
            except Exception:
                pass

    async def _call(session, name: str, arguments: dict):
        return await session.send_request(
            types.CallToolRequest(params=types.CallToolRequestParams(
                _meta={}, name=name, arguments=arguments)),
            types.CallToolResult)

    def _meta_extra(meta) -> dict:
        """兼容两种 SDK 形态：compat types 的 _meta 是 dict，版本化模型是
        ResultMetaObject（extra="allow"，扩展键在 model_extra）。"""
        if meta is None:
            return {}
        if isinstance(meta, dict):
            return meta
        return meta.model_extra or {}

    ctx, s = await _session(token)
    try:
        # L1. discover 协商（无版本头 = 官方握手姿势）
        _record("L1 discover→adopt 握手", True)

        # L1b. discover instructions + 契约版本（OPT-010/OPT-011，Task 5.6）：
        #      SDK 原生 instructions 字段可读；_meta 命名空间键声明契约版本
        disc2 = await s.send_request(
            types.DiscoverRequest(params=types.RequestParams(_meta={})), types.DiscoverResult)
        _record("L1b discover.instructions", bool(disc2.instructions),
                (disc2.instructions or "")[:24])
        contract_v = _meta_extra(disc2.meta).get("io.homebookshelf/contractVersion")
        _record("L1b 契约版本 _meta", contract_v in ("v1", "v2"), str(contract_v))

        # L2. tools/list 呈现 + outputSchema
        tl = await s.send_request(
            types.ListToolsRequest(params=types.PaginatedRequestParams(_meta={})),
            types.ListToolsResult)
        names = [t.name for t in tl.tools]
        _record("L2 tools/list 双工具", names == ["bookshelf_search_books", "bookshelf_get_book"],
                str(names))
        _record("L2 outputSchema 已声明", all(t.output_schema for t in tl.tools))

        # 1. 搜索命中
        tc = await _call(s, "bookshelf_search_books", {"query": "三体"})
        hit = "".join(c.text or "" for c in tc.content)
        _record("1 搜索命中", "三体" in hit and tc.structured_content["count"] == 1)
        book_id = tc.structured_content["items"][0]["id"]

        # 2. 详情读取
        td = await _call(s, "bookshelf_get_book", {"book_id": book_id})
        _record("2 详情读取", td.structured_content["title"] == "三体")

        # 3. 空条件拒绝（isError 稳定结构）
        te = await _call(s, "bookshelf_search_books", {"query": "   "})
        _record("3 空条件拒绝", te.is_error
                and "至少提供一个" in "".join(c.text or "" for c in te.content))

        # 3b. _meta 稳定错误码（OPT-012）：官方 SDK CallToolResult 对未知顶层
        #     字段 extra="ignore"（structuredError 被丢弃），但 result._meta 的
        #     ResultMetaObject extra="allow"——io.homebookshelf/error 原样可读
        meta_err = _meta_extra(te.meta).get("io.homebookshelf/error")
        _record("3b _meta 稳定错误码",
                bool(meta_err) and meta_err.get("code") == "QUERY_REQUIRED"
                and meta_err.get("request_id", "").startswith("req_"),
                str(meta_err))

        # 3c. 输入契约封闭（BUG-230）：member_id 等未知参数必须被 PARAM_INVALID
        #     明确拒绝，不得静默忽略后当作正常结果返回
        tmi = await _call(s, "bookshelf_search_books",
                          {"query": "三体", "member_id": 3})
        mi_err = _meta_extra(tmi.meta).get("io.homebookshelf/error")
        _record("3c 未知参数明确拒绝",
                tmi.is_error and (mi_err or {}).get("code") == "PARAM_INVALID",
                str(mi_err))

        # 4. Cursor 翻页
        tp1 = await _call(s, "bookshelf_search_books", {"query": "刘", "limit": 1})
        cur = tp1.structured_content["next_cursor"]
        tp2 = await _call(s, "bookshelf_search_books", {"query": "刘", "limit": 1, "cursor": cur})
        ids = {tp1.structured_content["items"][0]["id"],
               tp2.structured_content["items"][0]["id"]}
        _record("4 Cursor 翻页", tp1.structured_content["count"] == 1 and len(ids) == 2)

        # 5. 不存在书目（防枚举语义）
        tnf = await _call(s, "bookshelf_get_book", {"book_id": 999999})
        _record("5 不存在书目", tnf.is_error
                and "未找到可访问" in "".join(c.text or "" for c in tnf.content))

        # 9. 隐私哨兵：输出无敏感键。BUG-230 起 description 文案会显式声明
        #    "不接受 member_id 等身份参数"（有意为之的教学文本），因此键名
        #    哨兵只扫描结构性字段（name/inputSchema/outputSchema/annotations）
        #    与工具调用结果；description 自然语言不在键名扫描范围。敏感值
        #    哨兵（真实家庭数据）另由 tests/mcp/test_contract.py 全量覆盖。
        tl2 = await s.send_request(
            types.ListToolsRequest(params=types.PaginatedRequestParams(_meta={})),
            types.ListToolsResult)
        tl2_dump = tl2.model_dump(mode="json", by_alias=True)
        tl2_structural = {
            "tools": [
                {k: v for k, v in t.items() if k != "description"}
                for t in tl2_dump.get("tools", [])
            ],
        }
        all_text = json.dumps([tl2_structural,
                               tc.model_dump(mode="json", by_alias=True)], ensure_ascii=False)
        sentinels = ["member_id", "file_path", "cover_path", "reading_notes", "purchase",
                     "channel", "location", "isbn13", "isbn10", "extra"]
        _record("9 隐私哨兵零命中", not any(x in all_text for x in sentinels))
    finally:
        await _close(ctx, s)

    # 6. 越权 Grant（混合 scope → HTTP 403 拒绝）
    ctx2, s2 = await _session(mixed_token)
    try:
        try:
            await _call(s2, "bookshelf_search_books", {"query": "三体"})
            _record("6 越权 Grant 拒绝", False, "未拒绝")
        except Exception as exc:
            _record("6 越权 Grant 拒绝", True, type(exc).__name__)
    finally:
        await _close(ctx2, s2)

    # 10. REST/MCP 一致性：同一试点 Grant 在 REST 也放行（GET 只读）
    try:
        req = urllib.request.Request(f"{base}/api/v1/books", headers=auth_headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            _record("10 REST-MCP 一致（试点双放行）", resp.status == 200, str(resp.status))
    except Exception as exc:
        _record("10 REST-MCP 一致（试点双放行）", False, str(exc))

    # L3. 精确路径：/mcp/ 404、GET 405
    st, _ = _raw_post(base, "/mcp/", {"jsonrpc": "2.0", "id": 1,
                                      "method": "tools/list", "params": {"_meta": {}}},
                      auth_headers)
    _record("L3 /mcp/ 精确 404", st == 404, str(st))
    try:
        req = urllib.request.Request(f"{base}/mcp", method="GET")
        urllib.request.urlopen(req, timeout=5)
        st3 = 200
    except Exception as exc:
        st3 = getattr(exc, "code", 0)
    _record("L3 GET /mcp 405", st3 == 405, str(st3))

    # L4. 错误协议：未知方法 -32601；通知（无 id）静默 202
    st4, body4 = _raw_post(base, "/mcp", {"jsonrpc": "2.0", "id": 1,
                                          "method": "initialize", "params": {"_meta": {}}},
                           proto_headers)
    _record("L4 未知方法 -32601", st4 == 200 and "-32601" in body4, f"st={st4}")
    st5, _ = _raw_post(base, "/mcp", {"jsonrpc": "2.0",
                                      "method": "notifications/initialized",
                                      "params": {"_meta": {}}}, proto_headers)
    _record("L4 通知静默 202", st5 == 202, str(st5))

    # L5. Mcp-Name 路由头（SDK 自动附加，server 校验一致）
    ctx4, s4 = await _session(token)
    try:
        tn = await _call(s4, "bookshelf_get_book", {"book_id": book_id})
        _record("L5 Mcp-Name 头一致性", tn.structured_content["title"] == "三体")
    finally:
        await _close(ctx4, s4)

    # 8. 限流：耗尽额度 → 429
    codes = []
    for i in range(65):
        st, _ = _raw_post(base, "/mcp", {"jsonrpc": "2.0", "id": i,
                                         "method": "tools/list", "params": {"_meta": {}}},
                          proto_headers)
        codes.append(st)
    _record("8 限流 429", 429 in codes, f"codes={codes[-6:]}")

    # 7. 撤销：吊销 Grant → 下一请求立即失效（连握手都被拒）
    rev_code = f'''
import sys, os
sys.path.insert(0, {str(BACKEND_DIR)!r})
from app import db as db_module
from app.services import agent_access as aa
with db_module.SessionLocal() as db:
    aa.revoke_grant(db, int(open({str(tmp / "grant_id.txt")!r}).read().strip()))
print("REVOKED")
'''
    subprocess.run([sys.executable, "-c", rev_code], check=True,
                   env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp / 'e2e.db'}"})
    try:
        ctx3, s3 = await _session(token)
        await _close(ctx3, s3)
        _record("7 撤销后请求失效", False, "撤销后仍可握手")
    except Exception as exc:
        _record("7 撤销后请求失效", True, f"握手被拒({type(exc).__name__})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--serve", action="store_true",
                    help="只引导不起评估：起临时库 uvicorn 并保持运行，打印 URL 与"
                         "试点 Token 供真实客户端（Claude Code / Inspector）实机连接；"
                         "Ctrl-C 退出后自动清理临时库（--keep 保留）")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="mcp-cf-"))
    port = args.port or _free_port()
    proc = None
    try:
        _seed(tmp)
        token = (tmp / "token.txt").read_text().strip()
        proc = _start_server(tmp, port)
        if args.serve:
            print(f"SERVE_READY base=http://127.0.0.1:{port} token={token}")
            print("按 Ctrl-C 停止并清理临时库")
            try:
                proc.wait()
            except KeyboardInterrupt:
                pass
            return 0
        mixed_token = (tmp / "mixed_token.txt").read_text().strip()
        asyncio.run(_evaluate(f"http://127.0.0.1:{port}", token, mixed_token, tmp))
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        if not args.keep:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for r in RESULTS if r["ok"])
    report = {
        "tool": "official mcp SDK client",
        "sdk_version": "2.1.0",
        "protocol": "2026-07-28",
        "summary": {"pass": passed, "fail": len(RESULTS) - passed,
                    "result": "PASS" if passed == len(RESULTS) else "FAIL"},
        "items": RESULTS,
    }
    print(f"\n==== 结果: {passed}/{len(RESULTS)} ====")
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"报告已写入 {args.report}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())