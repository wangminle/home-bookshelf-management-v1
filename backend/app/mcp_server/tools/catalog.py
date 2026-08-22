"""MCP 核心只读工具（WBS-MCP-5）：bookshelf_search_books / bookshelf_get_book。

复用 catalog_read 共享 Read Model；输出模型在 L1 白名单之上再收紧：
- 不返回 cover_thumbnail_url（MCP 设计 §6.1：首期不返回封面 URL，避免
  绕过 Agent 鉴权与审计的匿名封面路径）；
- 不返回 public_tags（CHK-071：标签无公开分级前不下发）；
- search 至少一个筛选条件（禁止空条件遍历全库，MCP 设计 §6.1）；
- 游标由服务端 HMAC 签发/校验（独立密钥），绑定页码防篡改。

CHK-077 补丁：
- BUG-210：字符串入参先 strip 再做空/条件判定，纯空白搜索条件一律
  QUERY_REQUIRED，不得以 LIKE '%%' 遍历全库；
- BUG-212：inputSchema 的 maxLength 在运行时强制校验；页长统一走
  mcp_effective_max_page_size（配置越界收敛）；游标长度设硬上限；
- BUG-216：两个工具在 tools/list 中声明 outputSchema（JSON Schema
  2020-12 子集），成功结果经内置轻量校验器验证后才返回（不新增
  jsonschema 依赖）。
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

# 入参运行时硬上限（BUG-212：Schema 声明必须在边界强制，而非仅文档）
_QUERY_MAX_LENGTH = 200
_FILTER_MAX_LENGTH = 100
_LANGUAGE_MAX_LENGTH = 20
# 游标格式为 v1.<page>.<digest12>.<sig16>，正常长度 ~40；超过即拒绝
_CURSOR_MAX_LENGTH = 128

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

# ── 输出 Schema（BUG-216/WBS Task 5.2：structuredContent 的冻结形状） ──

_BOOK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "title": {"type": "string"},
        "subtitle": {"type": ["string", "null"]},
        "authors": {"type": "array", "items": {"type": "string"}},
        "translators": {"type": "array", "items": {"type": "string"}},
        "publisher": {"type": ["string", "null"]},
        "publish_date": {"type": ["string", "null"]},
        "edition": {"type": ["string", "null"]},
        "language": {"type": ["string", "null"]},
        "page_count": {"type": ["integer", "null"]},
        "category": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
        "availability": {"type": "string", "enum": ["in_shelf", "borrowed", "unknown"]},
    },
    "required": list(_MCP_OUTPUT_FIELDS),
    "additionalProperties": False,
}

_SEARCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": _BOOK_OUTPUT_SCHEMA},
        "count": {"type": "integer", "minimum": 0},
        "has_more": {"type": "boolean"},
        "next_cursor": {"type": ["string", "null"]},
    },
    "required": ["items", "count", "has_more", "next_cursor"],
    "additionalProperties": False,
}


class ToolError(Exception):
    """业务错误：以 isError=true 的稳定结构返回（MCP 设计 §11.2）。"""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _validate_against_schema(value: Any, schema: dict[str, Any], path: str) -> None:
    """轻量 JSON Schema 2020-12 子集校验（不引入 jsonschema 依赖）。

    支持：type（字符串或数组）/enum/const/properties/required/
    additionalProperties/items/minimum/maximum/minLength/maxLength。
    校验失败抛 ValueError（带路径），由调用方转换为稳定错误。
    """
    expected = schema.get("type")
    if expected is not None:
        types = [expected] if isinstance(expected, str) else list(expected)
        type_ok = False
        for t in types:
            if t == "string":
                type_ok = type_ok or isinstance(value, str)
            elif t == "integer":
                type_ok = type_ok or (isinstance(value, int) and not isinstance(value, bool))
            elif t == "number":
                type_ok = type_ok or (isinstance(value, (int, float)) and not isinstance(value, bool))
            elif t == "boolean":
                type_ok = type_ok or isinstance(value, bool)
            elif t == "array":
                type_ok = type_ok or isinstance(value, list)
            elif t == "object":
                type_ok = type_ok or isinstance(value, dict)
            elif t == "null":
                type_ok = type_ok or value is None
        if not type_ok:
            raise ValueError(f"{path}: 期望类型 {expected}，实际 {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: 值不在枚举范围内")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: 值与常量不符")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path}: 长度小于 minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path}: 长度超过 maxLength")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path}: 小于 minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path}: 大于 maximum")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValueError(f"{path}: 缺少必填字段 {key}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _validate_against_schema(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"{path}: 不允许的额外字段 {key}")

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _validate_against_schema(item, schema["items"], f"{path}[{i}]")


def validate_tool_output(payload: Any, schema: dict[str, Any]) -> None:
    """成功结果必须通过 outputSchema（BUG-216/WBS Task 5.2 红灯）。"""
    try:
        _validate_against_schema(payload, schema, "$")
    except ValueError as exc:
        raise ToolError(
            "OUTPUT_SCHEMA_MISMATCH",
            "工具输出与服务端冻结契约不一致，已拒绝下发",
            retryable=False,
        ) from exc


def tool_descriptors() -> list[dict[str, Any]]:
    """tools/list 描述符（顺序固定：search -> get；Schema 不含真实家庭数据）。"""
    return [
        {
            "name": "bookshelf_search_books",
            "description": SEARCH_DESCRIPTION,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": _QUERY_MAX_LENGTH},
                    "author": {"type": "string", "maxLength": _FILTER_MAX_LENGTH},
                    "category": {"type": "string", "maxLength": _FILTER_MAX_LENGTH},
                    "language": {"type": "string", "maxLength": _LANGUAGE_MAX_LENGTH},
                    "availability": {"type": "string", "enum": ["in_shelf", "borrowed", "unknown"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "cursor": {"type": "string"},
                },
                "required": [],
            },
            "outputSchema": _SEARCH_OUTPUT_SCHEMA,
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
            "outputSchema": _BOOK_OUTPUT_SCHEMA,
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
    if len(cursor) > _CURSOR_MAX_LENGTH:
        raise ToolError("INVALID_CURSOR", "游标长度超限")
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


def _mcp_item(summary: dict[str, Any]) -> dict[str, Any]:
    out = {k: summary[k] for k in _MCP_OUTPUT_FIELDS if k != "availability"}
    out["availability"] = summary.get("availability_status", "unknown")
    return out


# 字符串入参的运行时长度上限（与 inputSchema 声明一致，BUG-212）
_STRING_LIMITS = {
    "query": _QUERY_MAX_LENGTH,
    "author": _FILTER_MAX_LENGTH,
    "category": _FILTER_MAX_LENGTH,
    "language": _LANGUAGE_MAX_LENGTH,
}


def _clean_string_arguments(arguments: dict[str, Any]) -> dict[str, str | None]:
    """类型校验 + strip + 长度校验（BUG-204/210/212）。

    - 非字符串（含 bool）-> PARAM_INVALID；
    - strip 后超长 -> PARAM_INVALID（Schema 声明在边界强制）；
    - 返回 strip 后的值（空白条件不会进入 LIKE '%%' 全库遍历）。
    """
    cleaned: dict[str, str | None] = {}
    for key, limit in _STRING_LIMITS.items():
        value = arguments.get(key)
        if value is None:
            cleaned[key] = None
            continue
        if isinstance(value, bool) or not isinstance(value, str):
            raise ToolError("PARAM_INVALID", f"{key} 必须是字符串")
        value = value.strip()
        if len(value) > limit:
            raise ToolError("PARAM_INVALID", f"{key} 长度超过上限 {limit}")
        cleaned[key] = value or None
    return cleaned


def search_books(db: Session, arguments: dict[str, Any]) -> dict[str, Any]:
    # BUG-204/210/212：入参类型/空白/长度校验前移到工具边界
    cleaned = _clean_string_arguments(arguments)
    query = cleaned["query"]
    author = cleaned["author"]
    category = cleaned["category"]
    language = cleaned["language"]

    availability = arguments.get("availability")
    if availability is not None and availability not in ("in_shelf", "borrowed", "unknown"):
        raise ToolError("PARAM_INVALID", "availability 必须是 in_shelf/borrowed/unknown 之一")
    # BUG-210：strip 后判定--纯空白条件视同未提供，禁止空条件遍历全库
    if not any((query, author, category, language, availability)):
        raise ToolError(
            "QUERY_REQUIRED",
            "至少提供一个搜索或筛选条件（query/author/category/language/availability）",
        )
    # BUG-195：bool 是 int 子类，显式排除（limit=true/book_id=true 不得当数字用）
    limit = arguments.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolError("LIMIT_INVALID", "limit 必须是整数")
    # BUG-212：页长上限走收敛后的冻结契约（配置越界/运行时改写不放大上限）
    max_page = settings.mcp_effective_max_page_size
    if limit < 1 or limit > max_page:
        raise ToolError("LIMIT_INVALID", f"limit 必须在 1-{max_page} 之间")
    cursor = arguments.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ToolError("INVALID_CURSOR", "cursor 必须是字符串")
    if cursor is not None and len(cursor.strip()) != len(cursor):
        raise ToolError("INVALID_CURSOR", "游标格式无效")
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
    payload = {
        "items": items,
        "count": len(items),
        "has_more": result.has_more,
        "next_cursor": encode_cursor(page + 1, digest) if result.has_more else None,
    }
    # BUG-216：structuredContent 必须通过 outputSchema 才下发
    validate_tool_output(payload, _SEARCH_OUTPUT_SCHEMA)
    return payload


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
    payload = _mcp_item(detail.model_dump())
    # BUG-216：structuredContent 必须通过 outputSchema 才下发
    validate_tool_output(payload, _BOOK_OUTPUT_SCHEMA)
    return payload

# ── 封面 Resource（第三项分析中唯一可独立评估扩展；默认关闭） ──
# 受 Agent Grant 保护：走与工具完全相同的 Bearer/试点门禁/限流/审计，
# 绝不复用匿名封面 URL（/api/v1/public-catalog/covers/*）。

_COVER_URI_PREFIX = "bookshelf://covers/"
# 封面后缀 -> blob MIME（PIL 不可用时缩略图回退原图，须按真实后缀声明）
_COVER_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif",
}


def parse_cover_uri(uri: str) -> int:
    """解析 bookshelf://covers/{book_id}；格式不符抛 ToolError。"""
    if not isinstance(uri, str) or not uri.startswith(_COVER_URI_PREFIX):
        raise ToolError("RESOURCE_URI_INVALID", f"不支持的资源 URI（当前仅 {_COVER_URI_PREFIX}{{book_id}}）")
    raw = uri[len(_COVER_URI_PREFIX):]
    if isinstance(raw, bool) or not raw.isdigit() or int(raw) < 1:
        raise ToolError("RESOURCE_URI_INVALID", "资源 URI 的 book_id 必须是正整数")
    return int(raw)


def read_cover_resource(db: Session, book_id: int) -> dict:
    """读取封面缩略图为 MCP Resource blob（base64）。

    - 复用 public_catalog 的缩略图管线（PIL 缩放 + 缓存），但鉴权完全独立；
    - 超过 MCP_COVER_MAX_BYTES 拒绝（防大图撑爆 JSON-RPC 响应）；
    - 不存在/无封面/解码失败统一 COVER_NOT_FOUND（防枚举）。
    """
    import base64
    from pathlib import Path

    from app.config import settings
    from app.models import Book

    book = db.get(Book, book_id)
    if book is None or not book.cover_path:
        raise ToolError("COVER_NOT_FOUND", "未找到可访问的封面")
    # 路径解析与缩略图复用 Public Catalog 私有实现（同一安全管线，不同鉴权）
    from app.api.v1.public_catalog import _ensure_thumbnail, _resolve_cover

    src = _resolve_cover(book.cover_path)
    if src is None:
        raise ToolError("COVER_NOT_FOUND", "未找到可访问的封面")
    thumb = _ensure_thumbnail(src)
    try:
        blob_bytes = Path(thumb).read_bytes()
    except OSError:
        raise ToolError("COVER_NOT_FOUND", "未找到可访问的封面")
    if len(blob_bytes) > settings.mcp_cover_max_bytes:
        raise ToolError("COVER_TOO_LARGE", "封面过大，拒绝经 MCP 下发")
    mime = _COVER_MIME_BY_SUFFIX.get(thumb.suffix.lower(), "application/octet-stream")
    return {
        "uri": f"{_COVER_URI_PREFIX}{book_id}",
        "blob": base64.b64encode(blob_bytes).decode("ascii"),
        "mimeType": mime,
    }
