"""MCP 核心只读工具（WBS-MCP-5）：bookshelf_search_books / bookshelf_get_book。

复用 catalog_read 共享 Read Model；输出模型在 L1 白名单之上再收紧：
- 不返回 cover_thumbnail_url（MCP 设计 §6.1：首期不返回封面 URL，避免
  绕过 Agent 鉴权与审计的匿名封面路径）；
- 不返回 public_tags（CHK-071：标签无公开分级前不下发）；
- search 至少一个筛选条件（禁止空条件遍历全库，MCP 设计 §6.1）；
- 游标由服务端 HMAC 签发/校验（独立密钥），绑定页码防篡改。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.services import catalog_read

# v1 核心档工具 allowlist（WBS-MCP-0 Task 0.1：契约冻结）
MCP_TOOL_NAMES = ("bookshelf_search_books", "bookshelf_get_book")
_FORBIDDEN_TOOL_NAMES = frozenset({
    "list_all_books", "get_notes", "get_purchases", "get_attachments",
})

# MCP 输出字段 = Catalog 白名单 − 封面 URL − 标签（CHK-071/MCP §6.1）
_MCP_OUTPUT_FIELDS = (
    "id", "title", "subtitle", "authors", "translators", "publisher",
    "publish_date", "edition", "language", "page_count", "category",
    "summary", "availability",
)

SEARCH_DESCRIPTION = (
    "按关键词或结构化条件搜索家庭共享书目（L1 脱敏数据）。"
    "至少提供 query/author/category/language/availability 之一；"
    "需要 books:read 授权；不返回成员、阅读、笔记、购买或文件信息。"
)
GET_DESCRIPTION = (
    "在搜索拿到 book_id 后读取一本书的脱敏详情（L1/L2 白名单字段）。"
    "需要 books:read 授权；不返回封面 URL、文件路径或任何成员私有数据。"
)

_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def tool_descriptors() -> list[dict[str, Any]]:
    """tools/list 描述符（顺序固定：search → get；Schema 不含真实家庭数据）。"""
    return [
        {
            "name": "bookshelf_search_books",
            "description": SEARCH_DESCRIPTION,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 200},
                    "author": {"type": "string", "maxLength": 100},
                    "category": {"type": "string", "maxLength": 100},
                    "language": {"type": "string", "maxLength": 20},
                    "availability": {"type": "string", "enum": ["in_shelf", "borrowed", "unknown"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "cursor": {"type": "string"},
                },
                "required": [],
            },
            "annotations": dict(_ANNOTATIONS),
        },
        {
            "name": "bookshelf_get_book",
            "description": GET_DESCRIPTION,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "book_id": {"type": "integer", "minimum": 1},
                },
                "required": ["book_id"],
            },
            "annotations": dict(_ANNOTATIONS),
        },
    ]


# ── 游标（服务端签发/校验，独立密钥 HMAC；绑定页码 + 查询条件摘要） ──

def _cursor_secret() -> str:
    return settings.mcp_cursor_signing_secret or ""


def _filter_digest(filters: dict[str, Any], limit: int) -> str:
    """查询条件 + 页长的规范化摘要（BUG-201：游标不得跨条件复用）。"""
    canonical = json.dumps(
        {k: filters.get(k) for k in ("query", "author", "category", "language", "availability")},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(f"{canonical}|limit={limit}".encode("utf-8")).hexdigest()[:12]


def _sign(page: int, digest: str) -> str:
    return hmac.new(
        _cursor_secret().encode("utf-8"),
        f"mcp-cursor-v1:{page}:{digest}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16]


def encode_cursor(page: int, digest: str) -> str:
    return f"v1.{page}.{digest}.{_sign(page, digest)}"


def decode_cursor(cursor: str, digest: str) -> int:
    """校验并解析游标为页码；格式/签名/条件摘要不符抛 ToolError(INVALID_CURSOR)。"""
    parts = cursor.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        raise ToolError("INVALID_CURSOR", "游标格式无效")
    try:
        page = int(parts[1])
    except ValueError as exc:
        raise ToolError("INVALID_CURSOR", "游标格式无效") from exc
    if page < 1:
        raise ToolError("INVALID_CURSOR", "游标页码无效")
    if not hmac.compare_digest(parts[2], digest):
        raise ToolError("INVALID_CURSOR", "游标与当前查询条件不匹配")
    if not hmac.compare_digest(parts[3], _sign(page, digest)):
        raise ToolError("INVALID_CURSOR", "游标签名无效")
    return page


# ── 工具实现 ──


class ToolError(Exception):
    """业务错误：以 isError=true 的稳定结构返回（MCP 设计 §11.2）。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _mcp_item(summary: dict[str, Any]) -> dict[str, Any]:
    out = {k: summary[k] for k in _MCP_OUTPUT_FIELDS if k != "availability"}
    out["availability"] = summary.get("availability_status", "unknown")
    return out


def search_books(db: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    author = arguments.get("author")
    category = arguments.get("category")
    language = arguments.get("language")
    availability = arguments.get("availability")
    if not any((query, author, category, language, availability)):
        raise ToolError(
            "QUERY_REQUIRED",
            "至少提供一个搜索或筛选条件（query/author/category/language/availability）",
        )
    # BUG-195：bool 是 int 子类，显式排除（limit=true/book_id=true 不得当数字用）
    limit = arguments.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolError("LIMIT_INVALID", "limit 必须是整数")
    if limit < 1 or limit > settings.mcp_max_page_size:
        raise ToolError("LIMIT_INVALID", f"limit 必须在 1-{settings.mcp_max_page_size} 之间")
    cursor = arguments.get("cursor")
    filters = {
        "query": query, "author": author, "category": category,
        "language": language, "availability": availability,
    }
    digest = _filter_digest(filters, limit)
    page = decode_cursor(cursor, digest) if cursor else 1

    result = catalog_read.search_catalog(
        db,
        query=query, author=author, category=category,
        language=language, availability=availability,
        page=page, page_size=limit,
    )
    items = [_mcp_item(i.model_dump()) for i in result.items]
    return {
        "items": items,
        "count": len(items),
        "has_more": result.has_more,
        "next_cursor": encode_cursor(page + 1, digest) if result.has_more else None,
    }


def get_book(db: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    book_id = arguments.get("book_id")
    # BUG-195：排除 bool（True 是 int 子类）
    if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id < 1:
        raise ToolError("BOOK_ID_INVALID", "book_id 必须是 >= 1 的整数")
    detail = catalog_read.get_catalog_book(db, book_id)
    if detail is None:
        # 不区分不存在与不可见，防枚举（MCP 设计 §11.1）
        raise ToolError(
            "BOOK_NOT_FOUND",
            "未找到可访问的书目，请先用 bookshelf_search_books 确认 ID",
        )
    return _mcp_item(detail.model_dump())
