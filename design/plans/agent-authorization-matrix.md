# Agent 授权矩阵

> WBS-0 交付物：全部 API 端点 × 方法 × Scope × 主体 × 资源范围的完整映射。
> 端点清单以 `backend/app/main.py` 与 `backend/app/api/v1/` 实际路由为准（本文件已按实现核对）。

## Scope 定义

| Scope | 能力 | 默认建议 |
| --- | --- | --- |
| `books:read` | 查询书目与副本 | 可单独授予 |
| `books:write` | 入库、编辑书目、封面 | 需明确确认 |
| `books:delete` | 删除或合并书籍 | 高风险，默认不授予 |
| `reading:read` | 查看阅读进度和日志 | 可单独授予 |
| `reading:write` | 更新进度和日志 | 需明确确认 |
| `notes:read` | 查看笔记与附件 | 敏感，默认不授予 |
| `notes:write` | 新建或修改笔记 | 敏感，需明确确认 |
| `purchases:read` | 查看价格、渠道、订单信息 | 财务敏感，默认不授予 |
| `purchases:write` | 记录购买 | 需明确确认 |
| `stats:read` | 查看授权成员统计 | 可单独授予 |
| `files:read` | 下载授权范围附件 | 敏感 |
| `members:read` | 查看家庭成员 | 默认不授予普通 Agent |
| `stats:household` | 跨成员家庭统计 | 仅 owner 可授予 |

## 端点矩阵

| 端点 | 方法 | Scope | 主体 | 资源范围 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `/agent` | GET | 无（公开） | 匿名 | 无 | SPA 路由 |
| `/agent/bootstrap.md` | GET | 无（公开） | 匿名 | 无 | Agent 文本入口 |
| `/agent/manifest.json` | GET | 无（公开） | 匿名 | 无 | 机器清单 |
| `/.well-known/api-catalog` | GET | 无（公开） | 匿名 | 无 | RFC 9727 Linkset |
| `/llms.txt` | GET | 无（公开） | 匿名 | 无 | 精简导航 |
| `/agent/openapi.json` | GET | 无（公开） | 匿名 | 无 | allowlist 过滤 |
| `/agent/skills/index.json` | GET | 无（公开） | 匿名 | 无 | Skills 索引 |
| `/agent/skills/download/{version}.zip` | GET | 无（公开） | 匿名 | 无 | 限流 |
| `/agent/skills/SHA256SUMS` | GET | 无（公开） | 匿名 | 无 | 校验文件 |
| `/api/v1/public-health` | GET | 无（公开） | 匿名 | 无 | 最小可用性 |
| `/api/v1/health` | GET | `members:read` | owner | 全局 | 受保护诊断 |
| `/api/v1/books` | GET | `books:read` | agent/channel/web | 家庭共享 | |
| `/api/v1/books` | POST | `books:write` | agent/channel/web | 家庭共享 | |
| `/api/v1/books/{id}` | GET | `books:read` | agent/channel/web | 家庭共享 | |
| `/api/v1/books/{id}` | PATCH | `books:write` | agent/channel/web | 家庭共享 | |
| `/api/v1/books/{id}` | DELETE | `books:delete` | agent/channel/web | 家庭共享 | 高风险 |
| `/api/v1/books/{id}/merge` | POST | `books:delete` | agent/channel/web | 家庭共享 | 合并去重，高风险 |
| `/api/v1/books/{id}/cover` | POST | `books:write` | agent/channel/web | 家庭共享 | 设置封面 |
| `/api/v1/books/{id}/copies` | POST | `books:write` | agent/channel/web | 家庭共享 | |
| `/api/v1/books/{id}/progress` | POST | `reading:write` | agent/channel/web | 绑定成员 | |
| `/api/v1/books/{id}/purchases` | POST | `purchases:write` | agent/channel/web | 绑定成员 | |
| `/api/v1/books/{id}/notes` | POST | `notes:write` | agent/channel/web | 绑定成员 | |
| `/api/v1/books/{id}/reading-logs` | POST | `reading:write` | agent/channel/web | 绑定成员 | |
| `/api/v1/books/intake` | POST | `books:write` | agent/channel/web | 家庭共享 | |
| `/api/v1/books/intake/json` | POST | `books:write` | agent/channel/web | 家庭共享 | |
| `/api/v1/recognize/isbn` | POST | `books:write` | agent/channel/web | 无数据写入 | |
| `/api/v1/recognize/cover` | POST | `books:write` | agent/channel/web | 无数据写入 | |
| `/api/v1/attachments` | POST | `notes:write` | agent/channel/web | 绑定成员 | |
| `/api/v1/custom-fields` | POST | `books:write` | agent/channel/web | 家庭共享 | |
| `/api/v1/stats` | GET | `stats:read` | agent/channel/web | 绑定成员 | |
| `/api/v1/members` | GET | `members:read` | agent/channel/web | 绑定成员 | |
| `/api/v1/members` | POST | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/api/v1/members/bind` | POST | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/api/v1/files/covers/{filename}` | GET | `files:read` | agent/channel/web | 继承父资源 | |
| `/api/v1/files/attachments/{file_path}` | GET | `files:read` | agent/channel/web | 继承父资源 | |
| `/agent-access/clients` | GET | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/clients` | POST | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/clients/{id}` | DELETE | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/grants` | GET | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/grants` | POST | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/grants/{id}` | GET | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/grants/{id}` | PATCH | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/grants/{id}` | DELETE | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/tokens` | POST | 无（owner 专用） | owner/web | 全局 | 签发 Token |
| `/agent-access/tokens/{grant_id}` | GET | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/agent-access/tokens/{token_id}` | DELETE | 无（owner 专用） | owner/web | 全局 | 授权管理 |
| `/auth/status` | GET | 无（公开） | 匿名 | 无 | 是否已初始化 Owner 密码 |
| `/auth/init-password` | POST | 无（公开，限 loopback/`X-Setup-Token`） | 匿名 | 无 | Owner 密码初始化 |
| `/auth/login` | POST | 无（公开） | 匿名 | 无 | owner 登录 |
| `/auth/introspect` | GET | 无（需 Bearer Token） | agent | 无 | Token 自检 |
| `/auth/logout` | POST | 无（已认证） | web | 无 | 登出 |
| `/auth/session` | GET | 无（已认证） | web | 无 | 会话状态 |

> 规划中但**未实现**的端点（勿依赖）：`GET /books/{id}/copies|progress|purchases|notes|reading-logs`（子资源读接口，数据经 `GET /books/{id}` 返回）、`PATCH /copies/{id}`、`DELETE /attachments/{id}`、`DELETE /custom-fields/{id}`、`GET /stats/household`、`POST /books/intake/photo`。

## 禁止通配

Agent Grant 不支持 `*` 或 `admin:*` 通配符。授权管理和系统配置永远只属于 owner 会话。

## 资源归属规则

- `books`、`book_copies`：家庭共享资源，获 `books:read` 的成员可查询。
- `reading_progress`、`reading_logs`、`reading_notes`、`purchase_records`：默认绑定 `member_id`，Agent 只能访问 Grant 绑定成员的数据。
- 附件继承父资源权限，不允许仅凭附件 ID 越权下载。
- `stats:read` 只返回 Grant 绑定成员的统计；跨成员聚合必须另获 `stats:household`。
- owner 可以为任一成员创建 Grant，但 Grant 建立后不能在请求体中切换成员。
