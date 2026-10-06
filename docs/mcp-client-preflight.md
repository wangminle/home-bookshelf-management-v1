# MCP 目标客户端预检（WBS-MCP-P0 交付物）

> 日期：2026-08-26（CHK-088 回填）；2026-08-27 双客户端 + Inspector 实机执行（CHK-093）
> 与 WBS-MCP-P0A 客户端升级复测（PLN-009，同日）
> 状态：**现代协议核心链路 go，跨客户端通用发布仍 no-go**。官方 Python
> MCP SDK client 2.1.0 合成环境 22/22 通过；OpenCode 作为 Agent 访问本地
> 部署实例的真实家庭数据闭环也全部通过。Inspector 2.4.0 **显式
> `protocolEra=modern` 复测通过**（默认 legacy 仍不兼容，属默认配置问题
> 而非能力缺失）。Claude Code 官方升级至 2.1.247 后 wiretap 首帧**仍为
> Legacy `initialize` + `2025-11-25` + 无 `_meta`**，握手失败；是否为该
> 客户端启动双代际兼容（WBS-MCP-11）属 Owner 决策，未批准前维持 no-go。

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
| 协议协商 | 发送/接受 `2026-07-28`；旧版本 400 后降级 | 通过（discover→adopt 协商成功） | **不通过**（实机发送 `initialize` + `2025-11-25`，无降级路径） |
| Bearer 认证 | 每请求携带 Token；401/403/429 呈现 | 通过（header 注入） | 通过（`--header` 注入，握手请求已携带） |
| server/discover | 完成握手并读取 serverInfo | 通过 | **不通过**（无 discover 实现，走 initialize） |
| 帧约束 | 每请求 `params._meta`；网关头与 body 一致 | 通过（SDK 自动带 `_meta` 与 `Mcp-Name`） | **不通过**（initialize 帧 `params` 无 `_meta`，400 `PARAMS_META_REQUIRED`） |
| tools/list | 收到 2 工具、顺序稳定、Schema 可解析 | 通过 | **未到达**（握手失败，工具未注册） |
| tools/call | search（含游标）与 get 调用成功；isError 可读 | 通过 | **未到达**（同上） |
| structuredContent | 读取结构化输出而非仅文本 | 通过 | 未到达 |
| 错误语义 | QUERY_REQUIRED/INVALID_CURSOR/BOOK_NOT_FOUND 呈现 | 通过（is_error + 文本） | 未到达 |
| 撤销 | 撤销 Grant 后 401 并停止重试 | 通过（握手即被拒） | 未到达 |

> MCP Inspector 2.4.0（`npx @modelcontextprotocol/inspector --cli`，2026-08-27 实机）
> 默认 `protocolEra=legacy`，因此线缆证据与客户端 B 一致：`initialize` +
> `2025-11-25` + 无 `_meta` → 400 `PARAMS_META_REQUIRED`。Inspector 2.4.0 本身已
> 提供 `protocolEra=legacy|auto|modern`；**同日 WBS-MCP-P0A 已完成显式 modern
> 复测并全通过**（见下文"WBS-MCP-P0A 复测记录"），"默认 Legacy 不兼容"仅是
> 配置问题，不是能力缺失。Web UI 未独立实机执行，只记录为共用传输核心的
> 同源推断。

## go / no-go 判据

- **go**：两个目标客户端在全部检查项通过，且撤销/限流/错误路径表现可接受；
- **no-go（任一即触发）**：
  1. 任一客户端无法完成 `server/discover`+`tools/call` 闭环；
  2. 客户端绕过 Bearer（如复用浏览器 Cookie 场景）仍期望成功；
  3. 客户端要求旧协议（非 allowlist 版本）或 Session 语义；
  4. 撤销 Token 后客户端仍能取得数据（缓存旁路）。

**当前结论：现代协议路径 go，标准跨客户端发布 no-go（2026-08-27，P0A 复测后）。**
官方 SDK client 合成数据 22/22 全过，OpenCode/SDK 在真实部署和真实家庭数据
上也完成 discover→tools/list→search/get→隐私哨兵→撤销的完整闭环；Inspector
2.4.0 显式 `protocolEra=modern` 已在同日 P0A 复测中全链路通过（默认 legacy
不兼容仅是配置问题）。这证明服务端 wire contract、业务白名单和 Grant 即时
失效均正常。但 Claude Code 官方升级至 2.1.247 后首帧仍为 Legacy
`initialize`，单一客户端兼容门禁未过——是否为其启动双代际兼容
（WBS-MCP-11）属 Owner 决策（PLN-010），未批准前不能签署对 Claude Code
通用兼容的 go。

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

### 客户端 B：Claude Code（已实机测试，兼容门禁未通过）

2.1.231 与官方升级后的 2.1.247 均已抓帧：首帧仍为 Legacy `initialize` +
`2025-11-25` + 无 `params._meta`，被 400 `PARAMS_META_REQUIRED` 拒绝，
工具未注册。配置示例如下，仅供对照；是否作为必需客户端启动双代际兼容
层见 `PLN-010` / `WBS-MCP-11`。

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
   未使用官方 SDK server 端（`mcp.server`）；SDK 已在
   `backend/requirements.txt` 作为运行时依赖锁定 `mcp==2.1.0`；
2. 未提供 `notifications/initialized` 等生命周期方法的显式处理（按通知 202 丢弃）；
3. ~~`structuredError` 字段在官方 SDK 2.1.0 的 `CallToolResult` 模型未建模~~
   （2026-08-27 OPT-012 已收口：稳定 `code/retryable/request_id` 额外放入
   `result._meta["io.homebookshelf/error"]`——`ResultMetaObject` 为
   `extra="allow"`，官方 SDK 原样可读并已实机验证；顶层 `structuredError`
   扩展保留作自定义客户端向后兼容）。

## 自动化验证结论（2026-08-22 复核；2026-08-26 SDK conformance 回填；2026-08-27 Task 5.6 增补与复审修复；2026-09-17 BUG-231 深冻增补）

> 历史验证记录，非当前版本的新验收：以下测试数量对应当时记录。2026-10-06 仅修订报告引用说明，未重新运行这些测试。

- `backend/tests/mcp/`：114 项全绿（含 BUG-208～216 修复回归与 Task 5.6
  专项：输入契约封闭、discover instructions、`_meta` 稳定错误码、
  v2 摘要档契约、anyOf 声明面拒绝混合形态（空结果合法）、空白筛选值 Schema/运行时
  一致、v1 线缆基线快照、BUG-231 深冻 descriptor_constraints 字段级
  常量与精确键集的 19 项对抗变异测试——保留 required 外壳、掏空/放宽
  字段定义的弱化描述符全部被拒绝）；
- **SDK conformance 实机**（`scripts/mcp-sdk-conformance.py`，官方 SDK 2.1.0 client，
  v1 与 v2 契约各 22/22 全过）：discover→adopt 握手、discover.instructions 与契约版本 `_meta`、
  tools/list + outputSchema、搜索命中、详情读取、空条件拒绝、`_meta` 稳定错误码、
  未知参数（member_id）明确拒绝、Cursor 翻页、不存在书目、越权 Grant 拒绝、
  撤销后失效、限流 429、隐私哨兵零命中、REST/MCP 一致性、精确路径、错误协议、
  通知静默、Mcp-Name 头；
- 早期原始报告：[2026-08-26 SDK conformance JSON](../design/achievements/mcp-sdk-conformance-report-20260826.json)，内容为 **18/18** 通过，仅支持当日早期验证结果，不能作为上述 v1/v2 各 **22/22** 的原始报告。
- 后续 v1/v2 各 22/22 属于 2026-08-27 的历史复测记录，见[现行 MCP 设计与 WBS](../design/plans/家庭图书管理系统-MCP接口设计与WBS-20260821.md)中的 Task 5.6 验证说明及 `task-list.md` 的 CHK-098/CHK-099；目前归档索引未收录对应的后续原始 JSON 报告。后续重新验收应另存带日期与被测版本的新报告，保留 2026-08-26 文件。

## 封面 Resource（默认关闭，2026-08-22 新增）

- URI：`bookshelf://covers/{book_id}`（`resources/read`，返回 base64 blob）；
- 前置：`MCP_COVER_RESOURCE_ENABLED=true`（默认 false）+ 试点 Grant；
- 不复用匿名封面 URL；实机验证前保持关闭。

## 结论记录

| 日期 | 客户端 | 结论 | 备注 |
| --- | --- | --- | --- |
| 2026-08-26 | 官方 Python SDK 2.1.0 | 18/18 通过 | wire contract 与规范客户端兼容 |
| 2026-08-26 | Claude Code 2.1.231 | 配置就绪，实机待跑通 | `-p` 需登录态；`.mcp.json` 识别正常 |
| 2026-08-27 | Claude Code 2.1.231 | **不通过** | 线缆级证据：`initialize`/`2025-11-25`/无 `_meta` -> 400；工具未注册 |
| 2026-08-27 | MCP Inspector 2.4.0 | **默认 Legacy 不通过**（CLI 实机） | ~~强制 modern 复测待执行~~ 已由同日 P0A 复测取代（下两行）；Web UI 未独立实机 |
| 2026-08-27 | OpenCode + 官方 SDK 2.1.0 | **通过（真实部署）** | 端口 18009，真实家庭数据；9 项闭环全过，测后撤销凭据并恢复 MCP 默认关闭 |
| 2026-08-27 | MCP Inspector 2.4.0（`protocolEra=modern`） | **通过**（P0A 复测） | 首帧 `server/discover` + `_meta`（2026-07-28）；tools/list、search/get、错误显示全通 |
| 2026-08-27 | Claude Code 2.1.247（官方升级后） | **不通过**（P0A 复测） | 2.1.231→2.1.247 官方升级流程后 wiretap 首帧仍 `initialize`/`2025-11-25`/无 `_meta` -> 400 |

## 真实部署 Agent 验证（2026-08-27）

OpenCode 以官方 `mcp==2.1.0` client 作为 Agent，在 Owner 授权的临时验收
窗口内访问本地部署实例（端口 18009）。该轮使用真实家庭书目，但不是
持续发布：测试后已撤销 Grant/Token，并恢复 `MCP_ENABLED=false`，`/mcp`
再次返回 404。

| 验证项 | 结果 |
| --- | --- |
| discover / 协议 | `home_bookshelf_mcp`，`2026-07-28` |
| tools/list | 仅两个核心只读工具，均带 `outputSchema` |
| 真实搜索和详情 | 返回 4 本真实书目，title/summary 正常 |
| 隐私哨兵 | 成员、路径、渠道、ISBN、笔记和购买字段零命中 |
| 防枚举 | 不存在书目稳定 `isError` |
| 越权参数 | `member_id` 未扩大数据范围；当时为静默忽略（当日已由 BUG-230 收口：现为稳定 `PARAM_INVALID` 明确拒绝并记审计，conformance 3c 用例持续验证） |
| Grant 撤销 | 下一请求立即失败 |

该轮证明现代协议下的真实部署可用性，不替代 Claude Code 和 Inspector
的目标客户端兼容门禁，也不自动构成标准跨客户端发布 go。

## 实机执行记录（2026-08-27，CHK-093）

### 环境与方法

- 服务端：`python3 scripts/mcp-sdk-conformance.py --serve`（临时库 + uvicorn，
  `MCP_ENABLED=true`，与 conformance 同一引导；Token/Grant 同种子规则）；
- 客户端 A：官方 Python SDK 2.1.0（backend venv，`requirements.txt` 锁定）；
- 客户端 B：Claude Code 2.1.231（本机 CLI，`claude mcp add --transport http` +
  嵌套 `claude -p` 会话）；
- Inspector：`npx @modelcontextprotocol/inspector@2.4.0 --cli`；
- 线缆证据：Python wiretap 代理（18005 -> 18004 转发并落盘请求/响应帧）。

### 结果

| 客户端 | 握手线缆帧 | 结果 |
| --- | --- | --- |
| SDK 2.1.0 | `server/discover`（每请求 `params._meta`，adopt 后带版本头） | discover->adopt->tools/list->tools/call 全通，search 返回《三体》 |
| Claude Code 2.1.231 | `initialize` + `protocolVersion: 2025-11-25`，`params` 无 `_meta` | 400 `PARAMS_META_REQUIRED`；会话内 `mcp__bookshelf__*` 工具未注册 |
| Inspector 2.4.0 | 同 Claude Code（同 TS SDK 栈） | 400 `PARAMS_META_REQUIRED`（`--metadata` 不作用于 initialize） |

门禁栈对照（curl 直接验证）：`initialize` 补齐 `_meta` + `2026-07-28` 头 ->
JSON-RPC `-32601`（方法已移除）；`tools/list` 带 `2025-11-25` 头 ->
400 `PROTOCOL_VERSION_REJECTED`。三层拒绝均为设计意图。

### 结论与复测条件

- 预检结论 **no-go**（判据 3：客户端要求旧协议/非 allowlist 版本）；
- MCP 继续默认关闭（`MCP_ENABLED=false`），与「业务代码完成、发布门禁未过」
  的现状一致；
- 先以 `protocolEra=modern` 重跑 Inspector 2.4.0，避免把默认 Legacy 配置误判为
  客户端缺少现代能力；
- Claude Code 先通过官方升级流程更新，然后以 wiretap 确认首帧是否实际
  变为 `server/discover`，不以版本号替代线缆证据；
- 若升级/强制 modern 后目标客户端仍只能使用 Legacy，且产品确认必须
  支持该客户端，才启动条件式双代际兼容 WBS。不允许只放宽 `_meta`
  或手工补一个 `initialize` 响应。

### 附注

- `scripts/mcp-sdk-conformance.py` 本次新增 `--serve` 模式（供真实客户端实机连接）
  并修复退出期 anyio cancel-scope 跨任务清理异常（撤销用例握手被拒时 ctx 泄漏，
  已改为同任务对称退出；修复后连跑 18/18 全过、无异常输出）。

## WBS-MCP-P0A 复测记录（2026-08-27，PLN-009）

按 §22.2 执行客户端升级与协议代际复测：先排除客户端默认配置与旧版本
问题，再决定是否值得让服务端承担双代际复杂度。

### 环境与方法

- 服务端：`python3 scripts/mcp-sdk-conformance.py --serve --port 18010`
  （临时库 + uvicorn，测后临时库与 Token 一并清理）；
- 线缆证据：Python wiretap 代理（18011 -> 18010，逐帧落盘请求/响应）；
- Inspector：`npx @modelcontextprotocol/inspector@2.4.0 --cli --config <file>`
  （CLI 无 `--protocolEra` 旗标；modern 档经 config 文件
  `mcpServers.<name>.protocolEra: "modern"` 显式指定，legacy 对照同法）；
- Claude Code：先 `claude update` 官方升级（2.1.231 -> 2.1.247），再
  `claude mcp add --transport http`（经 wiretap URL）+ 嵌套 `claude -p` 会话
  触发握手，测后 `claude mcp remove` 清理注册。

### 结果

| 客户端 | 首帧线缆证据 | 闭环结果 |
| --- | --- | --- |
| Inspector 2.4.0 `protocolEra=modern` | `server/discover`，`params._meta` 携带 `io.modelcontextprotocol/protocolVersion=2026-07-28` + clientInfo + clientCapabilities；带 `mcp-method` 路由头 | **全通**：discover（含 instructions/契约版本）→ tools/list（含 outputSchema）→ search 命中《三体》→ get 详情；错误用例（`member_id` 未知参数）正确显示 `isError` + `PARAM_INVALID`（`_meta["io.homebookshelf/error"]` 与 `structuredError` 双通道可见） |
| Inspector 2.4.0 `protocolEra=legacy`（对照） | `initialize` + `2025-11-25` + 无 `_meta` | 400 `PARAMS_META_REQUIRED`（与 CHK-093 一致，证明差异仅在 protocolEra 配置） |
| Claude Code 2.1.247（升级后） | `initialize` + `protocolVersion: 2025-11-25`，`params` 无 `_meta`（clientInfo=claude-code/2.1.247） | 400 `PARAMS_META_REQUIRED`；工具未注册 |

### 结论与决策建议

- Inspector：**能力支持 modern 且实测可用**；此前"不兼容"结论收窄为
  "默认 legacy 配置不兼容"。接入方须显式 modern（CLI 走 config 文件，
  Web UI 在连接设置选 Protocol Era = Modern；Web UI 仍未独立实机，
  记录为待选项）；
- Claude Code：**官方升级后仍只能 Legacy**（2.1.247 线缆证据）。服务端
  保持现代单代际；WBS-MCP-11（双代际兼容）维持条件式待办——仅当
  Owner 确认 Claude Code 为发布必需客户端时才批准启动，并重过安全评审；
- 通用发布 go/no-go 维持 **no-go**（判据 3），但阻断面已从"两个目标
  客户端 + Inspector"收窄为"Claude Code 单一客户端（待 Owner 决策）"；
- MCP 维持默认关闭；本轮复测使用临时库与专用 Token，测后已全部清理。
