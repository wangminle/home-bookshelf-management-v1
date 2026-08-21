# Agent 授权矩阵

> 全部 API 端点 × 方法 × Scope × 主体 × 资源层 × 数据范围的完整映射。
> 端点清单以 `backend/app/main.py` 与 `backend/app/api/v1/` 实际路由为准（本文件已按实现核对）。
> 权限阶段 0（2026-08-21）起以 [`../权限-数据分层与用户角色设计建议-20260820.md`](../权限-数据分层与用户角色设计建议-20260820.md) 为唯一基线：
> 本矩阵每行补齐"主体、动作、资源层、数据范围"定义（阶段 0 验收项），可执行对照见
> `backend/tests/test_permission_baseline_matrix.py::ENDPOINT_REGISTRY`。

## Scope 定义

| Scope | 能力 | 风险档 | 未来目标名（未启用） | 默认建议 |
| --- | --- | --- | --- | --- |
| `books:read` | 查询书目与副本 | 低 | `catalog:read` | 可单独授予 |
| `books:write` | 入库、编辑书目、封面 | 中 | `catalog:write`（副本/批处理未来拆 `copies:*`、`catalog:batch_update`） | 需明确确认 |
| `books:delete` | 删除或合并书籍 | **高** | `catalog:delete` | 默认不授予；仅 Owner 批准 |
| `reading:read` | 查看阅读进度和日志 | 中（L3） | `reading:read` | 可单独授予 |
| `reading:write` | 更新进度和日志 | 中（L3） | `reading:write` | 需明确确认 |
| `notes:read` | 查看笔记与附件 | 敏感（L3） | `notes:read` | 默认不授予 |
| `notes:write` | 新建或修改笔记 | 敏感（L3） | `notes:write` | 需明确确认 |
| `purchases:read` | 查看价格、渠道、订单信息 | 财务敏感（L3） | `purchases:read` | 默认不授予 |
| `purchases:write` | 记录购买 | 中（L3） | `purchases:write` | 需明确确认 |
| `stats:read` | 查看授权成员统计 | 低 | `stats:self` | 可单独授予 |
| `files:read` | 下载授权范围附件 | 敏感 | `files:read` | 默认不授予 |
| `members:read` | 查看家庭成员基本信息 | 中（L4 边缘；channel_bindings 仅 owner 可见） | `members:read_basic` | 默认不授予普通 Agent |
| `stats:household` | 跨成员家庭统计 | **高** | `stats:aggregate` | 仅 Owner 可授予 |

> 兼容映射集中维护于 `backend/app/services/permission_policy.py::SCOPE_COMPAT_MAP`（权限阶段 0 任务 4）：
> 当前**不启用**任何运行时重命名；未来统一迁移时映射必须集中配置、版本化并测试，
> MCP 与 REST/CLI 同步切换，不允许 MCP 先行。管理类能力（`members:manage`、`agent_grants:manage`、
> `security:configure`、`audit:full`、`backup:manage` 等）永远不进入 Agent Scope——管理 API 始终要求 Owner Web 会话。

## 角色能力集（权限阶段 0）

服务器内置能力表（`permission_policy.role_scopes`），渠道与 Web 身份均按此映射：

| 角色 | 能力集 | 说明 |
| --- | --- | --- |
| `owner` | 全量（= ALL_SCOPES） | 家庭系统管理员、唯一 Agent 授权批准者 |
| `member` | 全量 − {`books:delete`, `stats:household`} | 删除书目主记录与全家庭统计仅 Owner；其余日常能力保留 |
| Agent | Grant 显式勾选 ⊆ `AGENT_GRANTABLE_SCOPES` | 只能由 Owner 批准（服务层强制校验批准者角色） |

Grant 风险分级：`HIGH_RISK_SCOPES = {books:delete, stats:household}`——可授予但仅 Owner 批准；
建议较短有效期（基线 §7.5 高风险档 7 天，完整期限/约束落地属权限阶段 3）。

## 端点矩阵

| 端点 | 方法 | Scope | 主体 | 资源层 | 数据范围 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| `/agent` | GET | 无（公开） | 匿名 | L0 | 无 | SPA 路由 |
| `/agent/bootstrap.md` | GET | 无（公开） | 匿名 | L0 | 无 | Agent 文本入口 |
| `/agent/manifest.json` | GET | 无（公开） | 匿名 | L0 | 无 | 机器清单 |
| `/.well-known/api-catalog` | GET | 无（公开） | 匿名 | L0 | 无 | RFC 9727 Linkset |
| `/llms.txt` | GET | 无（公开） | 匿名 | L0 | 无 | 精简导航 |
| `/agent/openapi.json` | GET | 无（公开） | 匿名 | L0 | 无 | allowlist 过滤 |
| `/agent/skills/index.json` | GET | 无（公开） | 匿名 | L0 | 无 | Skills 索引 |
| `/agent/skills/download/{version}.zip` | GET | 无（公开） | 匿名 | L0 | 无 | 限流 |
| `/agent/skills/SHA256SUMS` | GET | 无（公开） | 匿名 | L0 | 无 | 校验文件 |
| `/api/v1/public-health` | GET | 无（公开） | 匿名 | L0 | 无 | 最小可用性；不含部署态势 |
| `/api/v1/health` | GET | `members:read` | owner/member/agent | L4 | 全局诊断 | 权限阶段 0 起附部署信任态势字段 |
| `/api/v1/books` | GET | `books:read` | agent/channel/web | L1/L2 | household_shared | |
| `/api/v1/books` | POST | `books:write` | agent/channel/web | L2 | household_shared | |
| `/api/v1/books/{id}` | GET | `books:read` | agent/channel/web | L1/L2 | household_shared | 敏感子资源按各自 scope 过滤 |
| `/api/v1/books/{id}` | PATCH | `books:write` | agent/channel/web | L2 | household_shared | |
| `/api/v1/books/{id}` | DELETE | `books:delete` | owner/agent（高危 Grant） | L2 | household_shared | 高风险 |
| `/api/v1/books/{id}/merge` | POST | `books:delete` | owner/agent（高危 Grant） | L2 | household_shared | **阶段 0 修正**：此前误用 books:write，现按本表执行（合并删源书，破坏性） |
| `/api/v1/books/{id}/cover` | POST | `books:write` | agent/channel/web | L2 | household_shared | 设置封面 |
| `/api/v1/books/{id}/copies` | POST | `books:write` | agent/channel/web | L2 | household_shared | |
| `/api/v1/books/{id}/progress` | POST | `reading:write` | agent/channel/web | L3 | self(member) | 归属本人 |
| `/api/v1/books/{id}/purchases` | POST | `purchases:write` | agent/channel/web | L3 | self(member) | 归属本人 |
| `/api/v1/books/{id}/notes` | POST | `notes:write` | agent/channel/web | L3 | self(member) | 归属本人 |
| `/api/v1/books/{id}/reading-logs` | POST | `reading:write` | agent/channel/web | L3 | self(member) | 归属本人 |
| `/api/v1/books/intake` | POST | `books:write` | agent/channel/web | L2 | household_shared | |
| `/api/v1/books/intake/json` | POST | `books:write` | agent/channel/web | L2 | household_shared | |
| `/api/v1/recognize/isbn` | POST | `books:write` | agent/channel/web | L0 | 无数据写入 | |
| `/api/v1/recognize/cover` | POST | `books:write` | agent/channel/web | L0 | 无数据写入 | |
| `/api/v1/attachments` | POST | `notes:write` | agent/channel/web | L3 | self(member) | 继承父资源 |
| `/api/v1/custom-fields` | POST | `books:write` | agent/channel/web | L2 | household_shared | |
| `/api/v1/stats` | GET | `stats:read` | agent/channel/web | L2/L3 | self(member)；家庭聚合另需 `stats:household` | 读路径成员隔离属阶段 2 |
| `/api/v1/members` | GET | `members:read` | agent/channel/web | L4 | members_basic | channel_bindings 仅 owner 可见（BUG-113） |
| `/api/v1/members` | POST | 无（owner 专用） | owner/web | L4 | 全局 | 引导期允许匿名创建首个成员 |
| `/api/v1/members/bind` | POST | 无（owner/本人/引导期） | owner/web/agent | L4 | 全局 | 授权管理 |
| `/api/v1/files/covers/{filename}` | GET | `files:read` | agent/channel/web | L1（缩略图）/L3（原件） | 继承父资源 | |
| `/api/v1/files/attachments/{file_path}` | GET | `files:read` | agent/channel/web | L3 | 继承父资源 | |
| `/agent-access/clients` | GET | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/clients` | POST | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/clients/{id}` | DELETE | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/grants` | GET | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/grants` | POST | 无（owner 专用） | owner/web | L4 | 全局 | **阶段 0**：服务层强制批准者为 owner |
| `/agent-access/grants/{id}` | GET | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/grants/{id}` | PATCH | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/grants/{id}` | DELETE | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/tokens` | POST | 无（owner 专用） | owner/web | L4 | 全局 | 签发 Token |
| `/agent-access/tokens/{grant_id}` | GET | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/agent-access/tokens/{token_id}` | DELETE | 无（owner 专用） | owner/web | L4 | 全局 | 授权管理 |
| `/auth/status` | GET | 无（公开） | 匿名 | L0 | 无 | 是否已初始化 Owner 密码 |
| `/auth/init-password` | POST | 无（公开，限 loopback/`X-Setup-Token`） | 匿名 | L4 | 无 | Owner 密码初始化 |
| `/auth/login` | POST | 无（公开） | 匿名 | L0 | 无 | owner 登录 |
| `/auth/introspect` | GET | 无（需 Bearer Token） | agent | L0 | 无 | Token 自检 |
| `/auth/logout` | POST | 无（已认证） | web | L0 | 无 | 登出 |
| `/auth/session` | GET | 无（已认证） | web | L0 | 无 | 会话状态 |

> 规划中但**未实现**的端点（勿依赖）：`GET /books/{id}/copies|progress|purchases|notes|reading-logs`（子资源读接口，数据经 `GET /books/{id}` 返回）、`PATCH /copies/{id}`、`DELETE /attachments/{id}`、`DELETE /custom-fields/{id}`、`GET /stats/household`、`POST /books/intake/photo`。

## 禁止通配

Agent Grant 不支持 `*` 或 `admin:*` 通配符。授权管理和系统配置永远只属于 owner 会话。

## 资源归属规则

- `books`、`book_copies`：家庭共享资源（L1/L2），获 `books:read` 的成员可查询。
- `reading_progress`、`reading_logs`、`reading_notes`、`purchase_records`：默认绑定 `member_id`（L3），Agent 只能访问 Grant 绑定成员的数据。
- 附件继承父资源权限，不允许仅凭附件 ID 越权下载。
- `stats:read` 只返回 Grant 绑定成员的统计；跨成员聚合必须另获 `stats:household`。
- owner 可以为任一成员创建 Grant，但 Grant 建立后不能在请求体中切换成员。

## 权限阶段 0 变更与发布说明（2026-08-21）

本节按基线 §13 管理阶段 0 的行为变更（均为**有意的安全收紧**，不提供恢复越权语义的开关）：

| 变更 | 受影响主体 | 旧行为 | 新行为 | 迁移动作 |
| --- | --- | --- | --- | --- |
| 渠道死分支修复：非 Owner 渠道按 member 能力集 | 绑定非 Owner 成员的渠道身份（IM 机器人等） | 无论绑定成员角色一律全量 Scope（`auth_context._build_from_channel_headers` 两分支均 ALL_SCOPES） | 按绑定成员角色映射：member 失去 `books:delete`、`stats:household`，其余保留 | 升级前运行 `python3 scripts/preview_permission_narrowing.py` 或 `bookshelf doctor` 查看受影响绑定；依赖删除/家庭统计的自动化改走 Owner Web 会话或单独的高风险 Agent Grant |
| Grant 批准者 Owner-only（服务层） | `create_grant` 调用方 | 未传批准者时默认"绑定成员自批" | 必须显式指定 Owner 批准者，否则 403 | 仅影响直接调用服务层的代码；HTTP 管理端点本就要求 Owner 会话 |
| merge 端点 Scope 修正 | 持有 `books:write`（无 `books:delete`）的 Agent/渠道 | 可调用合并（合并会删除源书） | 403，需 `books:delete` | 为确需合并的 Agent 补授 `books:delete`（Owner 决策） |
| `/health` 部署态势 | 无行为变化 | — | 新增 channel_signing_configured / channel_bindings_present / trusted_proxies_configured / public_base_url / public_url_https 只读字段 | 无；doctor 据此检查"渠道启用但无签名""反代但无 HTTPS"等不一致 |
| doctor 态势检查与缩权预览 | CLI 使用者 | — | 新增明文 HTTP 非回环、渠道签名缺失、反代/HTTPS 不一致告警；列出非 Owner 渠道绑定缩权预览 | 按告警提示补 `CHANNEL_SIGNING_SECRET` / `PUBLIC_BASE_URL` / HTTPS |

回滚：渠道缩权与 merge Scope 修正不提供回退开关（基线 §13：不恢复已知越权语义）；
如需临时关闭渠道身份能力，撤销对应渠道绑定即可。
