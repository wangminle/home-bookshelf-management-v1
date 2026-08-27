# MCP Agent 接入指引（OPT-011 / Task 5.6）

> 面向：以 Agent/MCP 客户端接入家庭书架只读服务的集成方。
> 配套：`server/discover` 返回的 `instructions` 字段与本文同源；
> 客户端兼容门禁见 `docs/mcp-client-preflight.md`。

## 1. 服务概览与前提

- 端点固定为 `POST /mcp`（无 SSE/GET 流，`/mcp/` 一律 404）；
- 仅支持协议版本 `2026-07-28`（首帧 `server/discover` 协商，后续请求带
  `MCP-Protocol-Version: 2026-07-28` 头）；
- 仅两个只读工具：`bookshelf_search_books`、`bookshelf_get_book`；
- 认证仅接受 `Authorization: Bearer <hbs_at_...>`（Owner 签发的试点
  Agent Token，Grant 必须恰好 `books:read` + `household_shared`）；
  Cookie 与渠道头会被拒绝；
- 数据边界固定为家庭共享脱敏白名单：**不含**成员、阅读记录、笔记、
  购买、文件路径、封面信息；不存在以参数选择成员身份的用法。

## 2. 推荐调用流程：search → get

1. `server/discover`（缺省版本头）协商版本，读取 `instructions` 与
   `result._meta["io.homebookshelf/contractVersion"]`（`v1` 或 `v2`）；
2. `tools/list` 获取两个工具的 `inputSchema`/`outputSchema`；
3. `bookshelf_search_books` 检索——**必须至少提供**
   `query/author/category/language/availability` 之一，否则
   `QUERY_REQUIRED`；
4. 需要完整字段时用 `bookshelf_get_book` 按 `id` 读取详情。

每个请求的 `params` 必须携带 `_meta` 对象（可空对象即可；官方 SDK
自动携带）。示例帧：

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "bookshelf_search_books",
            "arguments": {"query": "三体"}, "_meta": {}}}
```

## 3. 分页

`search` 返回 `count/has_more/next_cursor`：把 `next_cursor` 原样作为
下一次调用的 `cursor`（同一筛选条件、同一 `limit`）；游标由服务端
HMAC 签名并绑定查询条件与页长，换条件复用会得到 `INVALID_CURSOR`。
不要自行解码或改写游标。

## 4. 序列化与输出契约

- 成功结果读 `result.structuredContent`（JSON 对象）；
  `content[0].text` 是同一 JSON 的字符串形式，仅作展示；
- 两个工具均声明 `outputSchema`，字段为冻结白名单
  （`additionalProperties=false`）；出现契约外字段即为异常；
- **v2 契约**（`contractVersion=v2` 时）：`search` 支持可选
  `output="summary"`，items 只含 `id/title/authors/category/availability`
  五个摘要字段以降低上下文消耗；省略 `output` 时业务结果与字段集与
  v1 相同（声明面 items 为 oneOf：完整形态 13 字段全量或摘要形态恰
  5 字段，混合形态非法），`bookshelf_get_book` 始终返回全字段。v1 下
  传 `output` 会被 `PARAM_INVALID` 拒绝。

## 5. 错误处理（稳定错误码）

`isError=true` 时按以下优先级读取结构化错误，**不要解析本地化文本做
逻辑分支**：

1. `result._meta["io.homebookshelf/error"]`
   → `{code, retryable, request_id}`——官方 SDK 2.1.0 的
   `CallToolResult` 对未知顶层字段 `extra="ignore"`，但 `_meta` 会被
   原样保留，这是官方 SDK 唯一可直接读到的位置；
2. `result.structuredError`（顶层扩展字段，自定义客户端向后兼容）；
3. `content[0].text`（人类可读文案，最后手段）。

常见 code：

| code | 含义 | retryable | 处理建议 |
| --- | --- | --- | --- |
| `PARAM_INVALID` | 参数类型/长度非法，或**未知参数**（含 `member_id` 等身份参数） | 否 | 修正参数后重试 |
| `QUERY_REQUIRED` | 未提供任何筛选条件 | 否 | 至少加一个条件 |
| `LIMIT_INVALID` | limit 不在 1-20 | 否 | 调整页长 |
| `INVALID_CURSOR` | 游标格式/签名/条件不符 | 否 | 从第一页重新检索 |
| `BOOK_ID_INVALID` | book_id 非正整数 | 否 | 修正 ID |
| `BOOK_NOT_FOUND` | 书目不存在或不可见（防枚举，不区分两者） | 否 | 先 search 确认 ID |
| `DB_BUSY` | 数据库暂时不可用 | 是 | 退避后重试 |
| `OUTPUT_SCHEMA_MISMATCH` | 服务端输出自检失败（不应出现） | 否 | 附 request_id 上报 |

HTTP 层错误（`X-Error-Code` 头）：401 `AUTH_REQUIRED`/`TOKEN_INVALID`、
403 `SCOPE_DENIED`/`PILOT_GRANT_REQUIRED`/`NETWORK_DENIED`、
429 `RATE_LIMITED`（带 `Retry-After`）、400 协议类、413 请求超限。

## 6. 官方 Python SDK 客户端示例

官方 SDK 2.1.0 的 `ClientSession` 显式会话模式（与服务端
`scripts/mcp-sdk-conformance.py` 及客户端预检使用的姿势一致，22/22 实测通过）：

```python
import asyncio
from mcp import ClientSession, types
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

TOKEN = "hbs_at_<public_id>_<secret>"
URL = "http://127.0.0.1:8000/mcp"

async def main() -> None:
    # 认证头必须经自定义 httpx 客户端注入（streamable_http_client 本身不带 headers 参数）
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    async with streamable_http_client(URL, http_client=http) as (read, write):
        async with ClientSession(read, write) as s:
            # 2026-07-28 握手：显式 server/discover + adopt 安装协商状态
            disc = await s.send_request(
                types.DiscoverRequest(params=types.RequestParams(_meta={})),
                types.DiscoverResult)
            s.adopt(disc)
            result = await s.send_request(
                types.CallToolRequest(params=types.CallToolRequestParams(
                    _meta={}, name="bookshelf_search_books",
                    arguments={"query": "三体"})),
                types.CallToolResult)
            if result.is_error:
                meta = result.meta if isinstance(result.meta, dict) \
                    else (result.meta.model_extra or {})
                print("stable error:", meta.get("io.homebookshelf/error"))
                return
            for item in result.structured_content["items"]:
                print(item["id"], item["title"])

asyncio.run(main())
```

> 常见错误（DOC-046）：
> - 高层 `Client` 的第一个参数是 **server/传输**（URL 字符串或 Transport），
>   不是客户端展示名——`Client("my-agent")` 会把 `"my-agent"` 当 URL 解析而
>   无法握手；展示名应放 `client_info=Implementation(name=..., version=...)`；
> - 高层 `Client("http://…/mcp")` 的 URL 分支不接收自定义 httpx 客户端，
>   无法注入 Bearer 头，因此本服务（强制 Bearer）请使用上面的
>   `ClientSession` 显式模式；
> - `streamable_http_client(...)` 返回的 `(read, write)` 必须传给
>   `ClientSession(read, write)`，否则会话没有传输可走。
>
> 兼容提示：compat 命名空间（`mcp.types`）的 `_meta` 是普通 dict，直接
> `result.meta.get("io.homebookshelf/error")`；版本化模型
> （`mcp_types._v2026_07_28`）中扩展键在 `meta.model_extra`。

## 7. 客户端配置注意（2026-08-27 实测）

- 官方 Python SDK 2.1.0：`mode="2026-07-28"` 直连可用（合成 +
  真实部署均验证）；
- MCP Inspector 2.4.0：默认 `protocolEra=legacy` 会被 400
  `PARAMS_META_REQUIRED` 拒绝；须显式 modern（CLI 经 `--config`
  文件的 `mcpServers.<name>.protocolEra: "modern"`，Web UI 在连接
  设置中选择 Protocol Era = Modern）。modern 下 discover/list/call
  与错误显示全部通过；
- Claude Code 2.1.247（官方升级流程后实测）：首帧仍为 Legacy
  `initialize` + `2025-11-25`，无法接入现代协议服务端；是否启动
  双代际兼容（WBS-MCP-11）由 Owner 决策；
- OpenCode + 官方 SDK：真实部署闭环验证通过。

## 8. 安全与限流基线

- 每请求级与工具级限流均为每分钟 60（429 + `Retry-After`）；
- Grant 撤销对下一请求立即生效（401）；
- 所有调用进入安全审计（含参数摘要与非明文 token 前缀）；
- 传入未知参数（例如试图以 `member_id` 指定身份）会被 `PARAM_INVALID`
  明确拒绝并留审计，不会静默忽略。
