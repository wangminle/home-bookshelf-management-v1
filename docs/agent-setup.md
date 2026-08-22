# 接入 Agent

家庭图书管理系统提供 **Agent Bootstrap Gateway**，让 AI Agent（如 Codex / OpenClaw / Hermes）在不接触业务数据的前提下发现系统能力、安装 Skills、申请授权并调用 API。

## 你将完成

1. 后端已部署并完成 Owner 密码初始化
2. Agent 通过 `/agent` 发现系统能力
3. 在 Web 授权中心创建 Agent 授权并获取 Token
4. Agent 使用 Bearer Token 调用 API

---

## 1. 初始化 Owner 密码

首次部署后，Owner 需设置密码：

```bash
# CLI 方式
cd backend && python -m app.admin owner-init-password

# 或通过 Web UI 访问 /agent-authorization 页面
```

设置后，Owner 通过统一登录页 `/login` 登录（权限阶段 2 起 Owner 与家庭成员使用同一入口；只有一个账号时用户名可留空）管理 Agent 授权。

---

## 2. Agent 发现入口

Agent 访问以下公开端点（无需认证）：

| 端点 | 说明 |
| --- | --- |
| `GET /agent/bootstrap.md` | Bootstrap Markdown，人类和 Agent 可读的能力概览（`/agent` 本身是前端 SPA 页面，返回 HTML） |
| `GET /agent/manifest.json` | 机器可读的系统清单（版本、能力、认证方式） |
| `GET /.well-known/api-catalog` | RFC 9727 API 目录（`application/linkset+json`） |
| `GET /agent/openapi.json` | Agent 专用 OpenAPI 规范（allowlist 过滤，仅含业务端点，不含管理端点） |
| `GET /agent/skills/index.json` | Skills 索引（名称、版本、scope） |
| `GET /agent/skills/download/{version}.zip` | Skills 包下载 |
| `GET /agent/skills/SHA256SUMS` | Skills 包校验文件 |
| `GET /llms.txt` | LLM 友好的系统说明 |

这些端点不返回任何业务数据（书籍、成员、购买记录等）。

---

## 3. 安装 Skills

```bash
# 1. 读 Skills 索引，拿到当前 bundle 版本和下载地址
curl http://<服务器>/agent/skills/index.json
# 响应含 bundle_version（如 0.2.5）与 archive_url；下载没有 latest.zip，只有带版本号的包

# 2. 按索引里的版本下载（以 0.2.5 为例）
curl -O http://<服务器>/agent/skills/download/0.2.5.zip
curl -O http://<服务器>/agent/skills/SHA256SUMS

# 3. 校验完整性（SHA256SUMS 里的文件名是 skills-0.2.5.zip，与下载文件对应）
shasum -a 256 -c SHA256SUMS

# 4. 解压：bundle 内含顶层 skills/ 目录，解到 Agent 根目录（避免 skills/skills 嵌套）
unzip skills-0.2.5.zip -d ~/.agent/
```

Skills 包含 9 个技能：

| Skill | Scope | 用途 |
| --- | --- | --- |
| `bookshelf-bootstrap` | - | 发现系统、安装 Skills、申请授权 |
| `bookshelf-setup` | - | 部署诊断、绑定引导 |
| `book-intake` | `books:write`, `files:read` | 入库 |
| `book-query` | `books:read` | 查询 |
| `reading-tracker` | `reading:write`, `books:read` | 进度 |
| `purchase-logger` | `purchases:write`, `books:read` | 购买 |
| `note-taker` | `notes:write`, `books:read` | 笔记 |
| `shelf-report` | `stats:read`, `books:read` | 统计 |
| `cover-eval` | - | 封面识书评测（本地脚本 + 视觉看图，不入库） |

---

## 4. 申请授权并获取 Token

### Web UI 方式（推荐）

1. Owner 在 `/login` 登录后进入 `/agent-authorization` 页面
2. 创建 Agent 客户端（名称 + 类型）
3. 创建授权（选择成员、Scope、有效期）
4. 签发 Token — **Token 仅显示一次，请立即保存**

### API 方式

```bash
# 1. Owner 登录获取 Cookie
curl -c cookies.txt -X POST http://<服务器>/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "your-username", "password": "your-password"}'

# 2. 创建 Agent 客户端
curl -b cookies.txt -X POST http://<服务器>/agent-access/clients \
  -H 'Content-Type: application/json' \
  -d '{"display_name": "My Agent", "client_type": "codex"}'

# 3. 创建授权（指定 scope 和有效期）
curl -b cookies.txt -X POST http://<服务器>/agent-access/grants \
  -H 'Content-Type: application/json' \
  -d '{"agent_client_id": 1, "member_id": 1, "scopes": ["books:read", "books:write"], "expires_in_days": 30}'

# 4. 签发 Token
curl -b cookies.txt -X POST http://<服务器>/agent-access/tokens \
  -H 'Content-Type: application/json' \
  -d '{"grant_id": 1}'
```

Token 格式为 `hbs_at_<public_id>_<secret>`，仅显示一次。

---

## 5. 使用 Token 调用 API

```bash
export BOOKSHELF_API_URL=http://<服务器>
export BOOKSHELF_TOKEN=hbs_at_xxx_yyy

# CLI 自动使用 Bearer Token
bookshelf add --title "活着" --author "余华"

# 或直接 HTTP 调用
curl -H "Authorization: Bearer $BOOKSHELF_TOKEN" \
  http://<服务器>/api/v1/books
```

可用 Scope（共 13 个）：

| Scope | 风险 | 说明 |
| --- | --- | --- |
| `books:read` | 低 | 查询书籍 |
| `books:write` | 中 | 创建/修改书籍 |
| `books:delete` | 高 | 删除书籍 |
| `reading:read` | 低 | 查看阅读进度 |
| `reading:write` | 中 | 更新阅读进度 |
| `notes:read` | 低 | 查看笔记 |
| `notes:write` | 中 | 创建笔记 |
| `purchases:read` | 低 | 查看购买记录 |
| `purchases:write` | 中 | 记录购买 |
| `stats:read` | 低 | 查看个人统计 |
| `stats:household` | 高 | 查看全家统计 |
| `files:read` | 低 | 读取附件 |
| `members:read` | 低 | 查看成员列表 |

---

## 6. 渠道头兼容（旧方式）

仍支持通过渠道头（`X-Channel` + `X-External-User-Id`）认证，适用于 IM Bot 等场景：

```bash
export BOOKSHELF_CHANNEL=feishu
export BOOKSHELF_EXTERNAL_USER_ID=ou_xxx
```

如果配置了 `CHANNEL_SIGNING_SECRET`，还需设置：

```bash
export BOOKSHELF_CHANNEL_SIGNING_SECRET=<与后端相同的密钥>
```

---

## 7. 常见卡点

| 现象 | 处理 |
| --- | --- |
| Agent 找不到 bookshelf | 检查 PATH / 虚拟环境；在 Agent 机器 `pip install -e cli` |
| Token 无效或已过期 | 在 Web 授权中心重新签发；检查 Grant 是否已过期或撤销 |
| 403 缺少 scope | 在 Web 授权中心修改 Grant 的 scope 列表（修改会撤销该 Grant 全部旧 Token，需重新签发） |
| 401 未认证 | 确保 Bearer Token 正确传入 `Authorization` 头 |
| Skills 下载失败 | 检查 `/agent/skills/index.json` 是否可访问 |
| doctor 报未授权 | 运行 `bookshelf auth status` 检查 Token 有效性 |

更多见 [FAQ](./faq.md)。

---

## 相关

- Skills 说明总览：[`skills/README.md`](../skills/README.md)
- Agent 授权管理页面：`/agent-authorization`
- Agent 连接信息页面：`/agent`

> 提示：业务端点读+写均需认证。给 Agent 的 Grant 除写 scope 外记得包含读 scope（如 `books:read`），否则 `find`/`show`/`stats` 等读命令会 403。
- 设计文档：[`design/plans/Agent引导入口与能力授权体系规划-20260812.md`](../design/plans/Agent引导入口与能力授权体系规划-20260812.md)
