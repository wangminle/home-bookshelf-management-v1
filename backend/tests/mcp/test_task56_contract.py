"""Task 5.6（CHK-096/BUG-230/OPT-010/OPT-011/OPT-012）契约测试。

- BUG-230：输入契约封闭——inputSchema additionalProperties=false + anyOf
  至少一个筛选条件；运行时在数据访问前拒绝未知键（含 member_id 等
  身份参数），稳定 PARAM_INVALID 并留审计；
- BUG-232：search inputSchema 的 anyOf 分支带非空白 pattern——空串/纯
  空白筛选值在机器 Schema（Draft 2020-12 实例级验证）即拒绝，与运行时
  "strip 后为空视同未提供"语义一致；
- OPT-010：v2 契约（MCP_CONTRACT_VERSION=v2）版本化拆分搜索摘要/详情；
  声明面 outputSchema 用 oneOf 精确表达 full/summary 两形态（BUG-231：
  混合形态在任一分支都不通过）；v1 兼容口径为业务输出与字段语义兼容
  （BUG-233），v1 线缆形状由 fixtures/v1_wire_baseline.json 基线快照固定
  （MCP_BASELINE_REGEN=1 重新生成）；
- OPT-011：server/discover 返回 instructions 与契约版本 _meta；
- OPT-012：工具错误在 result._meta["io.homebookshelf/error"] 携带稳定
  code/retryable/request_id（官方 SDK CallToolResult extra="ignore" 顶层
  扩展、ResultMetaObject extra="allow"），structuredError 向后兼容保留。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, Member
from app.services import rate_limit, security_audit
from app.utils.book_helpers import serialize_json_list
from app.mcp_server.tools import catalog as mcp_catalog

SENTINELS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "privacy_sentinels.json").read_text(encoding="utf-8")
)
_REPO_ROOT = Path(__file__).resolve().parents[3]
V2_SCHEMA_PATH = _REPO_ROOT / "design" / "schemas" / "mcp-catalog-tools-v2.schema.json"
V1_BASELINE_PATH = Path(__file__).resolve().parent / "fixtures" / "v1_wire_baseline.json"

_ERROR_META_KEY = "io.homebookshelf/error"
_CONTRACT_META_KEY = "io.homebookshelf/contractVersion"

V1_ITEM_FIELDS = {
    "id", "title", "subtitle", "authors", "translators", "publisher",
    "publish_date", "edition", "language", "page_count", "category",
    "summary", "availability",
}
SUMMARY_ITEM_FIELDS = {"id", "title", "authors", "category", "availability"}


@pytest.fixture(autouse=True)
def _reset_state():
    rate_limit.reset()
    security_audit.reset()
    yield
    rate_limit.reset()
    security_audit.reset()


@pytest.fixture()
def mcp_on(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_enabled", True)
    monkeypatch.setattr(settings, "mcp_cursor_signing_secret", "unit-test-cursor-secret-high-entropy")
    monkeypatch.setattr(settings, "mcp_allowed_hosts", "testserver")


@pytest.fixture()
def seeded(client: TestClient, db_session: Session) -> dict:
    owner_id = client.get("/auth/session").json()["member_id"]
    member = Member(name=SENTINELS["member_name"], role="member")
    db_session.add(member)
    db_session.commit()
    books = []
    for i in range(1, 3):
        b = Book(
            title=f"契约书{i}", authors=serialize_json_list([f"作者{i}"]),
            publisher=f"出版社{i}", category="科幻", language="zh",
            summary=f"契约测试摘要{i}",
        )
        db_session.add(b)
        db_session.commit()
        books.append(b)
    r = client.post("/agent-access/clients", json={"display_name": "契约 Agent"})
    agent_client_id = r.json()["id"]
    r = client.post("/agent-access/grants", json={
        "agent_client_id": agent_client_id, "member_id": owner_id,
        "scopes": ["books:read"], "data_scope": "household_shared",
    })
    grant_id = r.json()["id"]
    r = client.post("/agent-access/tokens", json={"grant_id": grant_id})
    return {"books": books, "token": r.json()["token"], "grant_id": grant_id}


def _mcp_client(db_session: Session) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=("127.0.0.1", 50000))


def _call(c: TestClient, token: str, name: str, arguments: dict):
    return c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments, "_meta": {}},
    }, headers={
        "Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28",
        "Authorization": f"Bearer {token}",
    })


def _discover(c: TestClient, token: str):
    return c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": {}},
    }, headers={"Authorization": f"Bearer {token}"})


# ── BUG-230：输入契约封闭 ──


def test_search_input_schema_closed_and_anyof_declared() -> None:
    schema = mcp_catalog.tool_descriptors()[0]["inputSchema"]
    assert schema["additionalProperties"] is False
    branches = [set(b.get("required", [])) for b in schema["anyOf"]]
    assert {"query"} in branches and {"availability"} in branches
    assert len(branches) == 5
    # 声明面不接受身份类参数
    assert not ({"member_id", "acting_for_member_id"} & set(schema["properties"]))


def test_get_input_schema_closed() -> None:
    schema = mcp_catalog.tool_descriptors()[1]["inputSchema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"book_id"}


def test_search_rejects_member_id_with_param_invalid(mcp_on, seeded: dict, db_session: Session) -> None:
    """CHK-095 发现的原始场景：member_id 越权意图必须被明确拒绝而非静默忽略。"""
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "契约书", "member_id": 3})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "PARAM_INVALID"
    assert "member_id" in result["structuredError"]["message"]
    assert "household_shared" in result["structuredError"]["message"]


def test_unknown_identity_param_alone_is_param_invalid_not_query_required(
        mcp_on, seeded: dict, db_session: Session) -> None:
    """只有 acting_for_member_id（无任何筛选条件）也先按未知键拒绝。"""
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {"acting_for_member_id": 1})
    assert r.json()["result"]["structuredError"]["code"] == "PARAM_INVALID"


def test_get_rejects_unknown_arguments(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_get_book",
              {"book_id": seeded["books"][0].id, "member_id": 3})
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["structuredError"]["code"] == "PARAM_INVALID"


def test_v1_rejects_output_param(mcp_on, seeded: dict, db_session: Session) -> None:
    """output 是 v2 契约参数：v1 下属于未知键，必须 PARAM_INVALID。"""
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "契约书", "output": "summary"})
    assert r.json()["result"]["structuredError"]["code"] == "PARAM_INVALID"


def test_unknown_param_rejection_is_audited(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    _call(c, seeded["token"], "bookshelf_search_books", {"query": "契约书", "member_id": 3})
    events = security_audit.list_security_events(db_session, event_type="mcp.call")
    assert any('"outcome": "deny"' in (e.payload or "") and "PARAM_INVALID" in (e.payload or "")
               for e in events)


# ── OPT-011：discover instructions 与契约版本 ──


def test_discover_instructions_and_contract_meta(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _discover(c, seeded["token"])
    assert r.status_code == 200
    result = r.json()["result"]
    instructions = result["instructions"]
    assert isinstance(instructions, str) and len(instructions) > 50
    for token in ("bookshelf_search_books", "bookshelf_get_book", "next_cursor",
                  _ERROR_META_KEY, "household_shared"):
        assert token in instructions
    assert result["_meta"][_CONTRACT_META_KEY] == "v1"
    # 静态文案不携带真实家庭数据
    for value in SENTINELS.values():
        assert value not in instructions


# ── OPT-012：_meta 稳定错误码（官方 SDK 可消费位置） ──


def test_tool_error_meta_carries_stable_code(mcp_on, seeded: dict, db_session: Session) -> None:
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {})
    result = r.json()["result"]
    assert result["isError"] is True
    meta_error = result["_meta"][_ERROR_META_KEY]
    assert meta_error["code"] == "QUERY_REQUIRED"
    assert meta_error["retryable"] is False
    assert meta_error["request_id"].startswith("req_")
    # 结构里只有稳定字段，不含本地化文本
    assert set(meta_error) == {"code", "retryable", "request_id"}
    # 向后兼容：顶层 structuredError 扩展仍在且一致
    assert result["structuredError"]["code"] == "QUERY_REQUIRED"
    assert result["structuredError"]["request_id"] == meta_error["request_id"]


def test_db_error_meta_via_schema_mismatch(mcp_on, seeded: dict, db_session: Session,
                                           monkeypatch) -> None:
    """OUTPUT_SCHEMA_MISMATCH（服务端契约自检失败）也必须带 _meta 稳定错误码。"""
    from unittest.mock import patch

    with patch.object(mcp_catalog, "validate_tool_output",
                      side_effect=mcp_catalog.ToolError(
                          "OUTPUT_SCHEMA_MISMATCH", "工具输出与服务端冻结契约不一致，已拒绝下发")):
        c = _mcp_client(db_session)
        r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "契约书"})
        result = r.json()["result"]
        assert result["isError"] is True
        assert result["_meta"][_ERROR_META_KEY]["code"] == "OUTPUT_SCHEMA_MISMATCH"


def test_cover_resource_error_meta(mcp_on, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_cover_resource_enabled", True)
    c = _mcp_client(db_session)
    r = c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "resources/read",
        "params": {"uri": "bookshelf://covers/notanumber", "_meta": {}},
    }, headers={
        "Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28",
        "Authorization": f"Bearer {seeded['token']}",
    })
    result = r.json()["result"]
    assert result["isError"] is True
    assert result["_meta"][_ERROR_META_KEY]["code"] == "RESOURCE_URI_INVALID"


# ── OPT-010：v2 版本化拆分（默认 v1，业务输出与字段语义兼容） ──


def _normalize_wire(obj: object) -> dict:
    """基线快照归一化：仅抹平跨运行必然变化的动态值。

    request_id（随机）、游标（HMAC）、serverInfo 版本（随构建变化）；
    其余字节（含描述文案、Schema、instructions、错误帧结构）原样保留。
    """
    text = json.dumps(obj, ensure_ascii=False)
    text = re.sub(r"req_[0-9a-f]{12}", "req_<id>", text)
    text = re.sub(r"v1\.\d+\.[0-9a-f]{12}\.[0-9a-f]{16}", "<cursor>", text)
    text = re.sub(
        r'("name": "home_bookshelf_mcp", "version": ")[^"]*(")',
        r"\1<version>\2", text,
    )
    return json.loads(text)


def _capture_v1_frames(c: TestClient, token: str, book_id: int) -> dict:
    """捕获 v1 模式下五类代表性线缆帧（discover/list/search/get/错误）。"""
    frames: dict[str, dict] = {}
    r = c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": {}},
    }, headers={"Authorization": f"Bearer {token}"})
    frames["discover"] = r.json()
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {}}}
    headers = {"Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28",
               "Authorization": f"Bearer {token}"}

    def _call(name: str, arguments: dict) -> dict:
        return c.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": {}},
        }, headers=headers).json()

    frames["tools_list"] = c.post("/mcp", json=rpc, headers=headers).json()
    frames["search_full"] = _call("bookshelf_search_books", {"query": "契约书"})
    frames["get_book"] = _call("bookshelf_get_book", {"book_id": book_id})
    frames["search_query_required"] = _call("bookshelf_search_books", {})
    return {k: _normalize_wire(v) for k, v in frames.items()}


def test_v1_semantic_compatibility_with_v2_off(mcp_on, seeded: dict, db_session: Session) -> None:
    """默认 v1：描述符无 output 参数、声明面仍是 13 字段严格 Schema、
    缺省搜索输出全字段——v2 不改变 v1 的业务输出与字段语义（BUG-233：
    兼容口径是字段/语义兼容，不是字节级不变；线缆形状由基线快照固定）。"""
    c = _mcp_client(db_session)
    r = c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {}},
    }, headers={
        "Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28",
        "Authorization": f"Bearer {seeded['token']}",
    })
    search = r.json()["result"]["tools"][0]
    assert "output" not in search["inputSchema"]["properties"]
    assert set(search["outputSchema"]["properties"]["items"]["items"]["required"]) == V1_ITEM_FIELDS
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "契约书"})
    items = r.json()["result"]["structuredContent"]["items"]
    assert items and set(items[0]) == V1_ITEM_FIELDS


def test_v1_wire_baseline_snapshot(mcp_on, seeded: dict, db_session: Session) -> None:
    """v1 线缆基线快照（BUG-233）：描述符、discover、错误帧、业务结果的
    归一化字节必须与冻结基线一致——此后对 v1 线缆的任何改动都必须
    有意更新基线，而不是无声漂移。MCP_BASELINE_REGEN=1 重新生成。
    """
    c = _mcp_client(db_session)
    captured = _capture_v1_frames(c, seeded["token"], seeded["books"][0].id)
    if os.environ.get("MCP_BASELINE_REGEN"):
        V1_BASELINE_PATH.write_text(
            json.dumps(captured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    assert V1_BASELINE_PATH.is_file(), "v1 线缆基线缺失；先 MCP_BASELINE_REGEN=1 生成"
    baseline = json.loads(V1_BASELINE_PATH.read_text(encoding="utf-8"))
    assert set(captured) == set(baseline)
    for key in sorted(captured):
        assert captured[key] == baseline[key], (
            f"v1 线缆帧 '{key}' 与冻结基线不一致（有意变更须 MCP_BASELINE_REGEN=1 "
            "重生成并在兼容报告中说明；回退无意漂移）"
        )


def test_v2_descriptor_summary_default_full(mcp_on, seeded: dict, db_session: Session,
                                            monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_contract_version", "v2")
    c = _mcp_client(db_session)
    r = c.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": {}},
    }, headers={
        "Content-Type": "application/json", "MCP-Protocol-Version": "2026-07-28",
        "Authorization": f"Bearer {seeded['token']}",
    })
    result = r.json()["result"]
    search = result["tools"][0]
    assert search["inputSchema"]["properties"]["output"]["enum"] == ["full", "summary"]
    # 声明面（BUG-231 + CHK-100 残余风险收口）：envelope 级 oneOf 两分支——
    # 完整响应（items 全为 13 字段形态）/ 摘要响应（items 全为恰 5 字段形态）；
    # 混合形态 item 或同响应混用两形态在任一分支都不通过
    envelope_one_of = search["outputSchema"]["oneOf"]
    assert len(envelope_one_of) == 2
    full_branch, summary_branch = envelope_one_of
    full_items = full_branch["properties"]["items"]["items"]
    assert set(full_items["required"]) == V1_ITEM_FIELDS
    assert full_items["additionalProperties"] is False
    summary_items = summary_branch["properties"]["items"]["items"]
    assert set(summary_items["required"]) == SUMMARY_ITEM_FIELDS
    assert summary_items["additionalProperties"] is False
    # 缺省 output：业务结果与字段集与 v1 一致（语义兼容）
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "契约书"})
    items = r.json()["result"]["structuredContent"]["items"]
    assert items and set(items[0]) == V1_ITEM_FIELDS


def test_v2_declared_schema_rejects_mixed_form(mcp_on, monkeypatch) -> None:
    """BUG-231 + CHK-100 残余风险：声明面 envelope 级 oneOf——完整/摘要
    响应合法，混合形态 item 与同响应混用两形态均非法。

    用与官方 SDK 客户端同源的 Draft 2020-12 验证器做实例级验证。
    """
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_contract_version", "v2")
    search = mcp_catalog.tool_descriptors()[0]
    declared = search["outputSchema"]
    validator = Draft202012Validator(declared)

    def _envelope(items: list[dict]) -> dict:
        return {"items": items, "count": len(items), "has_more": False, "next_cursor": None}

    full_item = {"id": 1, "title": "三体", "subtitle": None, "authors": ["刘慈欣"],
                 "translators": [], "publisher": None, "publish_date": None,
                 "edition": None, "language": None, "page_count": None,
                 "category": "科幻", "summary": None, "availability": "unknown"}
    summary_item = {"id": 1, "title": "三体", "authors": ["刘慈欣"],
                    "category": "科幻", "availability": "unknown"}
    # 服务端真实产生的两种响应都合法
    assert not list(validator.iter_errors(_envelope([full_item])))
    assert not list(validator.iter_errors(_envelope([summary_item])))
    # 混合形态 item（摘要字段 + 单个详情字段）在任一分支都不通过
    mixed_item = {**summary_item, "publisher": "重庆出版社"}
    assert list(validator.iter_errors(_envelope([mixed_item])))
    # 缺摘要必填字段的"详情子集"同样非法
    partial_item = {"id": 1, "title": "三体", "publisher": "重庆出版社"}
    assert list(validator.iter_errors(_envelope([partial_item])))
    # 同一响应混用 full/summary 对象（服务端按档位同构产出，不会出现）也非法
    assert list(validator.iter_errors(_envelope([full_item, summary_item])))


def test_v2_served_responses_pass_declared_schema(mcp_on, seeded: dict, db_session: Session,
                                                  monkeypatch) -> None:
    """官方 SDK 客户端按声明面 outputSchema 校验 structuredContent——
    线上 full/summary 两档真实响应都必须通过声明面（而非仅服务端严格档）。"""
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_contract_version", "v2")
    c = _mcp_client(db_session)
    search = mcp_catalog.tool_descriptors()[0]
    validator = Draft202012Validator(search["outputSchema"])
    for arguments in ({"query": "契约书"}, {"query": "契约书", "output": "summary"}):
        r = _call(c, seeded["token"], "bookshelf_search_books", arguments)
        assert r.json()["result"]["isError"] is False
        errors = list(validator.iter_errors(r.json()["result"]["structuredContent"]))
        assert not errors, [str(e) for e in errors]


def test_search_input_schema_matches_runtime_semantics(mcp_on, seeded: dict,
                                                       db_session: Session) -> None:
    """BUG-232：机器 Schema 与运行时对空白筛选值判定一致（实例级验证）。

    - 空串/纯空白条件在 Schema 层即拒绝（不再仅靠运行时 QUERY_REQUIRED）；
    - 空白 query + 有效 author 仍合法（与运行时"strip 后视同未提供"一致）；
    - 未知键在 Schema 层即拒绝（BUG-230 additionalProperties=false）。
    """
    schema = mcp_catalog.tool_descriptors()[0]["inputSchema"]
    validator = Draft202012Validator(schema)

    def valid(instance: dict) -> bool:
        return not list(validator.iter_errors(instance))

    assert not valid({}), "无条件请求必须在 Schema 层拒绝"
    assert not valid({"query": ""})
    assert not valid({"query": "   "})
    assert not valid({"limit": 5}), "仅分页参数、无筛选条件必须拒绝"
    assert valid({"query": "三体"})
    assert valid({"query": " 三体 "}), "含非空白字符即合法（运行时会 strip）"
    assert valid({"query": "   ", "author": "刘慈欣"}), "空白 query + 有效 author 合法"
    assert valid({"availability": "borrowed"})
    assert not valid({"query": "三体", "member_id": 3})

    # 与运行时交叉验证（HTTP 层）：两个分歧样例的行为必须与 Schema 判定一致
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books", {"query": "   "})
    assert r.json()["result"]["structuredError"]["code"] == "QUERY_REQUIRED"
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "   ", "author": "作者1"})
    assert r.json()["result"]["isError"] is False


def test_v2_summary_mode_trims_items(mcp_on, seeded: dict, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_contract_version", "v2")
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "契约书", "output": "summary"})
    assert r.json()["result"]["isError"] is False
    data = r.json()["result"]["structuredContent"]
    assert data["count"] == 2
    for item in data["items"]:
        assert set(item) == SUMMARY_ITEM_FIELDS
    # 详情档不受影响：get_book 仍返回全字段
    r = _call(c, seeded["token"], "bookshelf_get_book", {"book_id": seeded["books"][0].id})
    assert set(r.json()["result"]["structuredContent"]) == V1_ITEM_FIELDS


def test_v2_summary_pagination_and_invalid_value(mcp_on, seeded: dict, db_session: Session,
                                                 monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_contract_version", "v2")
    c = _mcp_client(db_session)
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "契约书", "limit": 1, "output": "summary"})
    data = r.json()["result"]["structuredContent"]
    assert data["has_more"] is True and data["next_cursor"]
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "契约书", "limit": 1, "cursor": data["next_cursor"], "output": "summary"})
    page2 = r.json()["result"]["structuredContent"]
    assert page2["count"] == 1 and set(page2["items"][0]) == SUMMARY_ITEM_FIELDS
    # 非法档位值
    r = _call(c, seeded["token"], "bookshelf_search_books",
              {"query": "契约书", "output": "compact"})
    assert r.json()["result"]["structuredError"]["code"] == "PARAM_INVALID"


def test_v2_contract_file_loads_and_freezes_surface(monkeypatch) -> None:
    """v2 契约文件可加载且冻结能力面；descriptor_constraints（BUG-231）
    以线上 tools/list 描述符为实例做 Draft 2020-12 验证——线上描述符
    偏离冻结契约即测试失败。"""
    from app.config import settings
    monkeypatch.setattr(settings, "mcp_contract_version", "v2")
    assert V2_SCHEMA_PATH.is_file(), f"v2 契约文件缺失: {V2_SCHEMA_PATH}"
    schema = json.loads(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["properties"]["contract_version"]["const"] == "v2"
    names = schema["properties"]["tools"]["items"]["properties"]["name"]["enum"]
    assert names == ["bookshelf_search_books", "bookshelf_get_book"]
    assert schema["properties"]["tools"]["maxItems"] == 2
    contains = schema["properties"]["unchanged_from_v1"]["contains"]["enum"]
    assert any("member_id" in item for item in contains)

    constraints = schema["properties"]["descriptor_constraints"]["properties"]
    for descriptor in mcp_catalog.tool_descriptors():
        validator = Draft202012Validator(constraints[descriptor["name"]])
        errors = list(validator.iter_errors(descriptor))
        assert not errors, [str(e) for e in errors]
