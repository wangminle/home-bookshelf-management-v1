# MCP 目标客户端预检（WBS-MCP-P0 交付物）

> 日期：2026-08-26（CHK-088 回填）
> 状态：**部分验证**——官方 Python MCP SDK client 2.1.0 已实机全链路通过（18/18）；
> Claude Code CLI 2.1.231 配置就绪（`.mcp.json` 已生成并识别）但 `-p` 工具调用实机
> 因需登录态未跑通；MCP Inspector 因 headless 环境未执行。
> 按设计 §15.3 规则，未实机验证的项目记"未验证"，不写"兼容"。

## 预检目标

在启用 `MCP_ENABLED=true` 连接真实家庭数据前，确认目标 Agent 客户端能以
本服务实现的契约完成握手、发现与调用，输出 go/no-go 结论。

## 当前服务契约（预检对象，CHK-088 已按官方 SDK 2.1.0 对齐）

- 传输：`POST /mcp`，无状态 JSON-RPC 2.0 over HTTP（无 SSE、无 Session 头）；
- 协议版本：`2026-07-28`。`server/discover` 是版本协商方法——**握手前可不带**
  `MCP-Protocol-Version` 头（官方 SDK 2.x 的 discover→adopt 流程），携带则须在
  allowlist；`tools/list`/`tools/call` 等**必须**携带该头且在 allowlist，否则 400；
- 传输安全：Host 校验（allowlist 外 421）+ Origin 精确匹配（不可信 403）+
  源地址门禁（`MCP_TRUSTED_CIDRS` 外 403 `NETWORK_DENIED`；可信代理按右值法
  解析 XFF）+ HTTPS 档（`MCP_REQUIRE_HTTPS` 默认 true，回环豁免）；
- 认证：每请求 `Authorization: Bearer <hbs_at_...>`（Cookie/渠道头被拒绝）；
- 帧约束：每个请求的 `params._meta` 必须为对象（缺失 400）；网关路由头
  `Mcp-Method`/`Mcp-Name`（如携带）必须与请求体一致，否则 400；
- 传输上限：请求/响应体各 1 MiB（请求超限 413，响应超限拒绝下发）；
- 方法：`server/discover` / `tools/list` / `tools/call`（`initialize` 已被该版本移除，
  返回 -32601；其余未知方法 -32601 或通知 202 丢弃）；
- DiscoverResult（2026-07-28）：顶层 `supportedVersions` / `resultType=discover` /
  `capabilities`（必填），`_meta.serverInfo` 为 display-only stamp；所有 result 带
  `ttlMs=0` / `cacheScope=private`（缓存指示）；
- 工具：`bookshelf_search_books`（必带筛选、limit≤20、签名游标）、
  `bookshelf_get_book`；`tools/list` 声明 `outputSchema`，输出
  `structuredContent` + 文本 `content`（结构化输出下发前经契约校验）；
- 专用 Grant 门禁：scopes 必须恰为 `{books:read}` **且 Grant 显式声明 data_scope=household_shared**，否则 403 `PILOT_GRANT_REQUIRED`（旧语义 Grant 一律拒绝）。

## 客户端能力矩阵（2026-08-26 回填）

| 检查项 | 判据 | 客户端 A：官方 Python SDK 2.1.0 | 客户端 B：Claude Code 2.1.231 |
| --- | --- | --- | --- |
| 版本 | 客户端版本与安装方式 | 2.1.0 / `pip install mcp==2.1.0` | 2.1.231 / npm 全局 CLI |
| 协议协商 | 发送/接受 `2026-07-28`；旧版本 400 后降级 | 通过（discover→adopt 协商成功） | 未验证 |
| Bearer 认证 | 每请求携带 Token；401/403/429 呈现 | 通过（header 注入） | 配置就绪 |
| server/discover | 完成握手并读取 serverInfo | 通过 | 未验证 |
| 帧约束 | 每请求 `params._meta`；网关头与 body 一致 | 通过（SDK 自动带 `_meta` 与 `Mcp-Name`） | 未验证 |
| tools/list | 收到 2 工具、顺序稳定、Schema 可解析 | 通过 | 未验证 |
| tools/call | search（含游标）与 get 调用成功；isError 可读 | 通过 | 未验证 |
| structuredContent | 读取结构化输出而非仅文本 | 通过 | 未验证 |
| 错误语义 | QUERY_REQUIRED/INVALID_CURSOR/BOOK_NOT_FOUND 呈现 | 通过（is_error + 文本） | 未验证 |
| 撤销 | 撤销 Grant 后 401 并停止重试 | 通过（握手即被拒） | 未验证 |

## go / no-go 判据

- **go**：两个目标客户端在全部检查项通过，且撤销/限流/错误路径表现可接受；
- **no-go（任一即触发）**：
  1. 任一客户端无法完成 `server/discover`+`tools/call` 闭环；
  2. 客户端绕过 Bearer（如复用浏览器 Cookie 场景）仍期望成功；
  3. 客户端要求旧协议（非 allowlist 版本）或 Session 语义；
  4. 撤销 Token 后客户端仍能取得数据（缓存旁路）。

**当前结论：仍为 no-go（待第二个目标客户端 + Inspector 实机）。** 官方 SDK client
18/18 全过仅证明 wire contract 与规范客户端兼容；按 WBS-MCP-P0 要求须两个客户端
均实机闭环后才可判 go。

## 目标客户端配置示例（CHK-088 新增）

### 客户端 A：官方 Python SDK（已验证）

```python
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client

TOKEN = "hbs_at_<public_id>_<secret>"  # 见下方 Token 安全保存说明
http = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
async with streamable_http_client("http://<host>:<port>/mcp", http_client=http) as (read, write):
    async with ClientSession(read, write) as s:
        disc = await s.send_request(
            types.DiscoverRequest(params=types.RequestParams(_meta={})), types.DiscoverResult)
        s.adopt(disc)  # 安装协商状态
        tools = await s.send_request(
            types.ListToolsRequest(params=types.PaginatedRequestParams(_meta={})), types.ListToolsResult)
        res = await s.send_request(
            types.CallToolRequest(params=types.CallToolRequestParams(
                _meta={}, name="bookshelf_search_books", arguments={"query": "三体"})),
            types.CallToolResult)
```

### 客户端 B：Claude Code（配置就绪，实机待跑通）

`.mcp.json`（项目级）：

```json
{
  "mcpServers": {
    "bookshelf": {
      "type": "http",
      "url": "http://127.0.0.1:18004/mcp",
      "headers": { "Authorization": "Bearer hbs_at_<public_id>_<secret>" }
    }
  }
}
```

CLI 添加：`claude mcp add --transport http bookshelf http://<host>:<port>/mcp --header "Authorization: Bearer <token>"`

## Token 配置与安全保存说明

- 令牌格式：`hbs_at_<public_id>_<secret>`，数据库仅存 SHA-256 摘要（明文只在签发时返回一次）；
- 安全保存：写入客户端密钥库（如 macOS Keychain、`claude` 的加密凭据存储），
  **不要**提交到 git 或写入公开文档；`.mcp.json` 若入库须用环境变量注入而非明文；
- 最小权限：仅授予试点 Grant（`books:read` + `data_scope=household_shared`），
  不授予混合 Scope 或旧语义 Grant；
- 撤销即失效：吊销 Grant 或缩小 Scope 会递增版本并令旧 Token 下一请求 401，
  客户端应停止重试并提示用户重新授权。

## 常见错误排查

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `400 PROTOCOL_VERSION_REQUIRED` | `tools/*` 缺 `MCP-Protocol-Version` 头 | 客户端握手后须带 `2026-07-28` 头 |
| `400 PARAMS_META_REQUIRED` | 请求 `params` 缺 `_meta` 对象 | 每请求补 `params._meta={}` |
| `400 HEADER_BODY_MISMATCH` | `Mcp-Method`/`Mcp-Name` 头与 body 不符 | 网关头须与 body 一致 |
| `401 AUTH_REQUIRED` / `TOKEN_INVALID` | 缺 Token / 令牌失效（撤销/版本变更/停用） | 重新签发 Token |
| `403 PILOT_GRANT_REQUIRED` | Grant 非专用试点（scopes≠`{books:read}` 或缺 data_scope） | 创建专用试点 Grant |
| `403 NETWORK_DENIED` / `HTTPS_REQUIRED` | 来源不在 `MCP_TRUSTED_CIDRS` / 非回环 HTTP | 配置 CIDR 或走 HTTPS |
| `421 HOST_REJECTED` | Host 不在 `MCP_ALLOWED_HOSTS` | 配置允许的 Host |
| `429 RATE_LIMITED` | 超出每分钟请求额度 | 客户端退避重试 |
| `-32601` | 方法未实现（如 `initialize`） | 使用 discover/tools/list/tools/call |
| `503 AUDIT_UNAVAILABLE` | 审计库不可用（fail-closed） | 检查后端数据库，恢复后重试 |

## 已知限制（当前实现，go 判定前必须知悉）

1. 传输层为自建最小 JSON-RPC 实现（经官方 SDK 2.1.0 client 验证 wire 兼容），
   未使用官方 SDK server 端（`mcp.server`）；SDK 已作为 dev 依赖锁定 `mcp==2.1.0`；
2. 未提供 `notifications/initialized` 等生命周期方法的显式处理（按通知 202 丢弃）；
3. `structuredError` 字段在官方 SDK 2.1.0 的 `CallToolResult` 模型未建模（错误码经
   `isError` + `content` 文本透出，自定义客户端仍可读原始 JSON 的 `structuredError`）。

## 自动化验证结论（2026-08-22 复核；2026-08-26 SDK conformance 回填）

- `backend/tests/mcp/`：73 项全绿（含 BUG-208～216 修复回归）；
- **SDK conformance 实机**（`scripts/mcp-sdk-conformance.py`，官方 SDK 2.1.0 client，
  18/18 全过）：discover→adopt 握手、tools/list + outputSchema、搜索命中、详情读取、
  空条件拒绝、Cursor 翻页、不存在书目、越权 Grant 拒绝、撤销后失效、限流 429、
  隐私哨兵零命中、REST/MCP 一致性、精确路径、错误协议、通知静默、Mcp-Name 头；
- 报告：`design/achievements/mcp-sdk-conformance-report-20260826.json`。

## 封面 Resource（默认关闭，2026-08-22 新增）

- URI：`bookshelf://covers/{book_id}`（`resources/read`，返回 base64 blob）；
- 前置：`MCP_COVER_RESOURCE_ENABLED=true`（默认 false）+ 试点 Grant；
- 不复用匿名封面 URL；实机验证前保持关闭。

## 结论记录

| 日期 | 客户端 | 结论 | 备注 |
| --- | --- | --- | --- |
| 2026-08-26 | 官方 Python SDK 2.1.0 | 18/18 通过 | wire contract 与规范客户端兼容 |
| 2026-08-26 | Claude Code 2.1.231 | 配置就绪，实机待跑通 | `-p` 需登录态；`.mcp.json` 识别正常 |
| — | MCP Inspector | 未执行 | headless 环境无法启动浏览器 |
