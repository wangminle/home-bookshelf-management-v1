"""WBS-MCP-0：契约冻结测试。

工具面、字段面、错误码在没有 SDK 的情况下可独立验证；
扩展能力必须新建版本化契约，不得原地放宽 v1 核心 allowlist。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.mcp_server.tools import catalog as mcp_catalog

_REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = _REPO_ROOT / "design" / "schemas" / "mcp-catalog-tools-v1.schema.json"
SENTINELS_PATH = Path(__file__).resolve().parent / "fixtures" / "privacy_sentinels.json"

FORBIDDEN_NAMES = [
    "list_all_books", "get_notes", "get_purchases", "get_attachments",
    "create_book", "update_book", "delete_book",
]


def _load_schema() -> dict:
    assert SCHEMA_PATH.is_file(), f"契约文件缺失: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_contract_file_exists_and_loads() -> None:
    schema = _load_schema()
    assert "tools" in schema["properties"]
    assert schema["required"] == ["tools"]


def test_tool_allowlist_is_frozen() -> None:
    schema = _load_schema()
    names_enum = schema["properties"]["tools"]["items"]["properties"]["name"]["enum"]
    contracted = set(names_enum)
    assert contracted == set(mcp_catalog.MCP_TOOL_NAMES)
    assert contracted == {"bookshelf_search_books", "bookshelf_get_book"}


def test_forbidden_names_never_in_contract_or_code() -> None:
    schema = _load_schema()
    forbidden = set(FORBIDDEN_NAMES)
    contains = schema["properties"].get("forbidden_tool_names", {}).get("contains", {}).get("enum", [])
    forbidden |= set(contains)
    descriptors = mcp_catalog.tool_descriptors()
    names = {d["name"] for d in descriptors}
    assert not (names & forbidden)
    assert not (set(mcp_catalog.MCP_TOOL_NAMES) & forbidden)


def test_descriptors_match_contract_shape() -> None:
    schema = _load_schema()
    allowed_names = schema["properties"]["tools"]["items"]["properties"]["name"]["enum"]
    for d in mcp_catalog.tool_descriptors():
        assert d["name"] in allowed_names
        ann = d["annotations"]
        assert ann["readOnlyHint"] is True
        assert ann["destructiveHint"] is False
        assert ann["idempotentHint"] is True
        assert ann["openWorldHint"] is False


def test_descriptors_contain_no_real_family_data() -> None:
    sentinels = json.loads(SENTINELS_PATH.read_text(encoding="utf-8"))
    dumped = json.dumps(mcp_catalog.tool_descriptors(), ensure_ascii=False)
    for value in sentinels.values():
        assert value not in dumped
    # 也不得用真实样例数据当 example/default
    assert "example" not in dumped and "default" not in dumped


def test_privacy_sentinels_fixture_complete() -> None:
    sentinels = json.loads(SENTINELS_PATH.read_text(encoding="utf-8"))
    for key in ("member_name", "private_note", "purchase_channel", "file_path",
                "cover_path", "token_like", "isbn", "custom_field", "tag"):
        assert sentinels.get(key), f"哨兵缺失: {key}"


def test_mcp_output_field_set_excludes_cover_and_tags() -> None:
    """MCP 输出在 L1 白名单之上再收紧：无封面 URL、无标签（设计 §6.1/CHK-071）。"""
    fields = set(mcp_catalog._MCP_OUTPUT_FIELDS)
    assert "cover_thumbnail_url" not in fields
    assert "public_tags" not in fields
    assert "availability" in fields
