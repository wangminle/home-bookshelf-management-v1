# MCP 目标客户端预检（WBS-MCP-P0 交付物）

> 日期：2026-08-21（CHK-072 后补）
> 状态：**未验证**——当前无实机客户端环境，本文是预检矩阵模板与 go/no-go 判据；
> 按设计 §15.3 规则，未实机验证的项目一律记"未验证"，不写"兼容"。

## 预检目标

在启用 `MCP_ENABLED=true` 连接真实家庭数据前，确认目标 Agent 客户端能以
本服务实现的契约完成握手、发现与调用，输出 go/no-go 结论。

## 当前服务契约（预检对象）

- 传输：`POST /mcp`，无状态 JSON-RPC 2.0 over HTTP（无 SSE、无 Session 头）；
- 协议版本：`2026-07-28`（`MCP-Protocol-Version` 请求头**必填**且须在 allowlist）；
- 传输安全：Host 校验（allowlist 外 421）+ Origin 精确匹配（不可信 403）+
  源地址门禁（`MCP_TRUSTED_CIDRS` 外 403 `NETWORK_DENIED`；可信代理按右值法
  解析 XFF）+ HTTPS 档（`MCP_REQUIRE_HTTPS` 默认 true，回环豁免）；
- 认证：每请求 `Authorization: Bearer <hbs_at_...>`（Cookie/渠道头被拒绝）；
- 帧约束：每个请求的 `params._meta` 必须为对象（缺失 400）；网关路由头
  `Mcp-Method`/`Mcp-Name`（如携带）必须与请求体一致，否则 400；
- 传输上限：请求/响应体各 1 MiB（请求超限 413，响应超限拒绝下发）；
- 方法：`server/discover` / `tools/list` / `tools/call`（`initialize` 已被该版本移除，
  返回 -32601；其余未知方法 -32601 或通知 202 丢弃）；
- 工具：`bookshelf_search_books`（必带筛选、limit≤20、签名游标）、
  `bookshelf_get_book`；`tools/list` 声明 `outputSchema`，输出
  `structuredContent` + 文本 `content`（结构化输出下发前经契约校验）；
- 专用 Grant 门禁：scopes 必须恰为 `{books:read}` **且 Grant 显式声明 data_scope=household_shared**，否则 403 `PILOT_GRANT_REQUIRED`（旧语义 Grant 一律拒绝）。

## 客户端能力矩阵（待实机填写）

| 检查项 | 判据 | 客户端 A：＿＿ | 客户端 B：＿＿ |
| --- | --- | --- | --- |
| 版本 | 客户端版本与安装方式 | 未验证 | 未验证 |
| 协议协商 | 发送/接受 `2026-07-28`；旧版本被 400 拒绝后的降级行为 | 未验证 | 未验证 |
| Bearer 认证 | 每请求携带 Token；401/403/429 的呈现与重试 | 未验证 | 未验证 |
| server/discover | 能完成握手并读取 result._meta.serverInfo | 未验证 | 未验证 |
| 帧约束 | 每请求携带 `params._meta` 对象；网关头与 body 一致 | 未验证 | 未验证 |
| tools/list | 收到 2 个工具且顺序稳定；Schema 可解析 | 未验证 | 未验证 |
| tools/call | search（含游标翻页）与 get 调用成功；isError 结构可读 | 未验证 | 未验证 |
| structuredContent | 客户端读取结构化输出而非仅文本 | 未验证 | 未验证 |
| 错误语义 | QUERY_REQUIRED/INVALID_CURSOR/BOOK_NOT_FOUND 的用户呈现 | 未验证 | 未验证 |
| 撤销 | 撤销 Grant 后客户端收到 401 并停止重试 | 未验证 | 未验证 |

## go / no-go 判据

- **go**：两个目标客户端在全部检查项通过，且撤销/限流/错误路径表现可接受；
- **no-go（任一即触发）**：
  1. 任一客户端无法完成 `server/discover`+`tools/call` 闭环；
  2. 客户端绕过 Bearer（如复用浏览器 Cookie 场景）仍期望成功；
  3. 客户端要求旧协议（非 allowlist 版本）或 Session 语义；
  4. 撤销 Token 后客户端仍能取得数据（缓存旁路）。

## 已知限制（当前实现，go 判定前必须知悉）

1. 未引入官方 Python MCP SDK——传输层为最小 JSON-RPC 实现，SDK 语义差异
   （如 Streamable HTTP 的 GET 流）未覆盖（`Mcp-Method`/`Mcp-Name` 网关头
   一致性已按协议实现并锁定回归测试）；
2. 未提供 `notifications/initialized` 等生命周期方法的显式处理（按通知 202 丢弃）；
3. `tools/list` 未声明 `ttlMs`/`cacheScope`（设计 §5.2 标记为待验证候选，未实现）。

## 自动化验证结论（2026-08-21；2026-08-22 复核）

以下不依赖实机客户端的项目已由自动化测试锁定（`backend/tests/mcp/`，67 项；
2026-08-22 复核，含 BUG-208～BUG-216 修复回归 24 项）：

- 契约 allowlist / 隐私哨兵零命中 / 工具发现顺序；
- 拒绝矩阵（无 Token/坏 Token/Cookie/渠道头/缺 Scope/非试点 Grant/限流/协议头/
  Host/Origin/源地址/HTTPS）；
- 撤销下一请求失效；游标条件绑定与防篡改；审计契约字段与 fail-closed；
- REST/MCP 语义一致性：同一 Grant 两入口同结果，MCP 恒为 REST 安全子集，
  限流 Profile 与匿名目录互不覆盖；
- BUG-208～216 回归：params._meta/网关头一致性/discover 形状、审计写后抑制
  与失败退避、空白搜索拒绝、未知方法计入全局限流、413 与响应上限、
  maxLength 与页宽夹取、游标密钥熵与复用拒绝、Grant 版本绑定与 Token 撤销、
  可信网络/HTTPS 门禁、DB 异常稳定映射、输出 Schema 校验与审计字段冻结。

## 结论记录

| 日期 | 客户端 | 结论 | 备注 |
| --- | --- | --- | --- |
| — | — | 未执行 | 无实机环境；启用真实数据前必须完成本预检并回填矩阵 |
