# 家庭图书管理系统 V2 · Home Bookshelf Management V2

**[🇨🇳 中文](#中文)** · **[🇬🇧 English](#english)**

> 面向家庭藏书的自托管管理系统：FastAPI 后端 + Vue 3 Web UI + Typer CLI + Agent 技能，支持 ISBN/拍照/书名入库、多源元数据聚合、阅读追踪与统计。
>
> A self-hosted home bookshelf manager: FastAPI backend + Vue 3 Web UI + Typer CLI + Agent skills. Intake by ISBN / photo / title, multi-source metadata, reading tracking & stats.

---

## 中文

### 核心功能

- **多方式入库**：ISBN 条码 / 书封照片 / 书名+作者，自动查重（ISBN + 规范化书名），ISBN-10/13 校验位验证
- **多源元数据聚合**：OpenLibrary · Google Books · 国图 NLC（中文 `9787` ISBN 自动路由）
- **副本与购买记录**：多副本管理、购买价格/渠道/订单号、花费统计
- **阅读追踪**：5 态进度（想读/在读/读完/弃读/放弃）、每日阅读日志、连续天数、读书笔记
- **Web UI**：Vue 3 SPA 封面墙浏览、筛选、详情页、阅读统计仪表盘、书架概览图生成与导出
- **附件**：书籍/副本/成员/笔记可挂链接、文件、Markdown
- **成员与 IM 绑定**：家庭成员 + 渠道白名单（飞书/Telegram 等）鉴权
- **识别与诊断**：封面/条码识别、`doctor` 自检

### 项目结构

```
home-bookshelf-management-v1/
├── backend/              FastAPI 后端
│   ├── app/
│   │   ├── api/v1/       路由（books/copies/intake/progress/purchases/notes/reading-logs/attachments/custom-fields/stats/members/recognize/files/health + web_auth/agent_access/agent_discovery/agent_skills）
│   │   ├── auth.py       渠道白名单鉴权（统一鉴权权威实现为 auth_context.py）
│   │   ├── services/     业务逻辑（intake/metadata/reading/cover_recognition/storage…）
│   │   ├── models/       SQLAlchemy 2.0 模型
│   │   ├── schemas/      Pydantic v2 schemas
│   ├── alembic/          数据库迁移（SQLite, WAL）
│   ├── static/           前端构建产物（gitignore，生产部署用）
│   ├── tests/            pytest 回归
│   ├── install.sh / install.bat
│   └── requirements.txt
├── frontend/             Vue 3 SPA（封面墙 / 详情 / 统计 / 概览图）
├── cli/                  Typer CLI（命令 bookshelf）
├── deploy/               docker-compose / systemd / backup.sh
├── skills/               Agent 技能（9 个）
├── design/               开发与需求文档（设计方案 / Schema / 调研 / 前端评估）
├── docs/                 用户说明（get-started / user-guide / web-ui / faq …）
├── AGENTS.md / CLAUDE.md
└── task-list.md
```

### 文档入口

- **使用说明**（`docs/`）：[快速开始](docs/get-started.md) · [使用指南](docs/user-guide.md) · [CLI 参考](docs/cli-reference.md) · [部署](docs/deployment.md) · [Web UI](docs/web-ui.md) · [接入 Agent](docs/agent-setup.md) · [FAQ](docs/faq.md)
- **设计与需求**：[AI-native 总体规划](design/plans/家庭图书管理系统AI-native系统框架和实施规划-20260822.md) · [权限与数据分层](design/plans/权限-数据分层与用户角色设计建议-20260820.md) · [MCP 设计与 WBS](design/plans/家庭图书管理系统-MCP接口设计与WBS-20260821.md) · [完整调研](design/discussions/家庭与中小组织AI-native系统框架调研报告-20260822.md) · [阶段复盘](design/checkpoints/README.md) · [历史成果](design/achievements/README.md)

### 后端安装与运行

```bash
cd backend
bash install.sh            # Windows: install.bat —— 建 venv、装依赖、跑迁移
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir .
```

- 条码识别需 zbar 运行库：macOS `brew install zbar`，Linux `apt-get install libzbar0`
- 可选 `GOOGLE_BOOKS_API_KEY` 提升中文书命中率
- API 文档：`http://<服务器IP>:8000/docs`

Docker 一键部署：

```bash
cd deploy
cp .env.example .env       # 按需改 BOOKSHELF_BIND / BOOKSHELF_DATA_DIR
docker compose up -d
```

### Web UI 构建

前端开发与构建详见 [Web UI 部署指南](docs/web-ui.md)。生产部署请用一键脚本（写入 `version.json`，`rsync --delete` 同步，排除 `skills/`）：

```bash
bash scripts/deploy_frontend.sh
# 路径别名（如 /home-bookshelf/）
bash scripts/deploy_frontend.sh --base /home-bookshelf/
```

详见 [路径别名部署](docs/web-ui.md#路径别名部署path-alias)。原生 `docker compose` 镜像已含前端构建阶段，无需再手工拷贝。

### CLI 命令（`bookshelf`）

| 命令 | 说明 |
| --- | --- |
| `add` | 入库（ISBN / 图片 / 书名+作者） |
| `find` / `show` | 搜索 / 详情 |
| `recognize` | 识别图片中的 ISBN |
| `progress` | 更新阅读进度（5 态/页码/百分比/评分） |
| `purchase` | 记录购买信息（`--original-price` 定价） |
| `note` / `reading-log` | 读书笔记 / 每日阅读日志 |
| `stats` | 藏书与阅读统计 |
| `member` | 新建家庭成员（`--name`/`--role`） |
| `doctor` | 初始化诊断（API/DB/Key/成员绑定） |
| `bind` | 绑定 IM 渠道账号到成员（白名单；空库首次 `--member-id 1` 会自动创建默认 owner） |
| `health` | 查看 API 状态 |
| `bootstrap` | 发现系统契约（manifest / Skills 索引 / public-health，无需认证） |
| `auth status` | 检查当前 Agent 授权状态（需 `BOOKSHELF_TOKEN`） |

### Skills（Agent 技能）

`skills/` 目录提供 9 个技能：`book-intake` · `book-query` · `bookshelf-bootstrap` · `bookshelf-setup` · `cover-eval` · `note-taker` · `purchase-logger` · `reading-tracker` · `shelf-report`。把该目录加入 Agent（OpenClaw / Hermes）的技能路径即可调用。

### Agent 使用指南

1. 部署后端（见上），运行 `bookshelf doctor` 确认全部通过
2. 安装 CLI：`pip install -e cli`
3. 指向后端：`export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000`
4. （可选）新建成员：`bookshelf member --name "你" --role owner`
5. 绑定成员（白名单）：`bookshelf bind --member-id 1 --channel feishu --external-user-id <渠道用户ID>`（空库首次绑定 `member_id=1` 会自动创建默认 owner）
6. 将 `skills/` 加入 Agent 技能路径，即可自然语言操作藏书

> ⚠️ **安全**：全部业务端点（读+写）均已接入统一鉴权（AuthContext），无凭证一律 401。认证方式三选一：Agent Bearer Token（`Authorization: Bearer ...`，按 Grant 的 scope 校验，见授权矩阵 `design/plans/agent-authorization-matrix.md`）、Web 会话 Cookie（Owner 密码登录，前端「Agent 授权」页设置）、渠道头 `X-Channel`/`X-External-User-Id`（须已绑定成员；可选 `CHANNEL_SIGNING_SECRET` 开启 HMAC 签名校验，CLI 用 `BOOKSHELF_CHANNEL_SIGNING_SECRET` 透传）。`X-UI-Client` 头不再有任何授权含义。Web Owner 会话可代表家庭成员操作；Agent/渠道身份只能操作绑定成员本人的数据。**v0.3.5 探活 breaking change**：`GET /api/v1/health` 需 `members:read`，无凭证返回 401。监控与 lwa 探活请改用 `curl -f http://<host>:<port>/api/v1/public-health`（Docker healthcheck 已切换）。引导期（尚无任何渠道绑定）仅 `POST /members` 与 `bind` 允许匿名初始化。仍建议**只在可信家庭局域网内运行，请勿暴露到公网**。权限阶段 1 起支持**匿名共享书架（C 模式）**：设置 `ANONYMOUS_CATALOG_MODE=lan_shared` 与 `TRUSTED_LAN_CIDRS` 后，可信局域网内访客可在 `/shared` 浏览脱敏书目（详见 `docs/deployment.md`）；不可信来源自动降级为登录页。权限阶段 4 起支持逐书可见级别（Owner 在详情页/策略页设置，`explicit_public` 模式下仅 `public` 标记的书匿名可见）。权限阶段 2 起 Owner 与家庭成员在 `/login` 使用各自的用户名+密码登录（成员账号由 Owner 创建/停用/重置，角色或密码变更后旧会话立即失效）。另有**默认关闭的 MCP 只读试点**（`/mcp`，书目搜索/详情两个工具，需专用试点 Grant 与独立游标密钥，配置见 `docs/deployment.md`）；官方 SDK 与目标客户端实测完成前不启用真实家庭数据。

---

## English

### Core Features

- **Flexible intake**: ISBN barcode / cover photo / title+author, with dedup (ISBN + normalized title) and ISBN-10/13 checksum validation
- **Multi-source metadata**: OpenLibrary · Google Books · NLC (auto-routes Chinese `9787` ISBNs)
- **Copies & purchases**: multiple copies, price/channel/order tracking, spending stats
- **Reading tracking**: 5-state progress (unread/reading/finished/abandoned/dropped), daily logs, streaks, notes
- **Web UI**: Vue 3 SPA with cover-wall browsing, filters, book details, reading stats dashboard, shelf overview export
- **Attachments**: link/file/markdown on books, copies, members, notes
- **Members & IM binding**: family members + channel whitelist (Feishu/Telegram) for auth
- **Recognition & diagnostics**: cover/barcode recognition, `doctor` self-check

### Project Structure

```
home-bookshelf-management-v1/
├── backend/              FastAPI backend
│   ├── app/
│   │   ├── api/v1/       routes (books/copies/intake/progress/purchases/notes/reading-logs/attachments/custom-fields/stats/members/recognize/files/health + web_auth/agent_access/agent_discovery/agent_skills)
│   │   ├── auth.py       channel whitelist auth (authoritative unified auth lives in auth_context.py)
│   │   ├── services/     business logic (intake/metadata/reading/cover_recognition/storage…)
│   │   ├── models/       SQLAlchemy 2.0 models
│   │   ├── schemas/      Pydantic v2 schemas
│   ├── alembic/          migrations (SQLite, WAL)
│   ├── static/           frontend build output (gitignored, for production)
│   ├── tests/            pytest regressions
│   ├── install.sh / install.bat
│   └── requirements.txt
├── frontend/             Vue 3 SPA (cover wall / details / stats / overview)
├── cli/                  Typer CLI (command: bookshelf)
├── deploy/               docker-compose / systemd / backup.sh
├── skills/               Agent skills (9)
├── design/               design & requirements
├── docs/                 user guides (get-started / user-guide / web-ui / faq …)
├── AGENTS.md / CLAUDE.md
└── task-list.md
```

### Docs

- User: [get-started](docs/get-started.md) · [user guide](docs/user-guide.md) · [FAQ](docs/faq.md) · [CLI](docs/cli-reference.md) · [deploy](docs/deployment.md) · [Web UI](docs/web-ui.md) · [agent](docs/agent-setup.md)
- Design: [AI-native plan](design/plans/家庭图书管理系统AI-native系统框架和实施规划-20260822.md) · [permissions](design/plans/权限-数据分层与用户角色设计建议-20260820.md) · [MCP](design/plans/家庭图书管理系统-MCP接口设计与WBS-20260821.md) · [research](design/discussions/家庭与中小组织AI-native系统框架调研报告-20260822.md) · [checkpoints](design/checkpoints/README.md) · [achievements](design/achievements/README.md)

### Backend Setup & Run

```bash
cd backend
bash install.sh            # Windows: install.bat — creates venv, installs deps, runs migrations
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir .
```

- Barcode recognition needs the zbar library: macOS `brew install zbar`, Linux `apt-get install libzbar0`
- Optional `GOOGLE_BOOKS_API_KEY` improves Chinese-book hit rate
- API docs: `http://<server-ip>:8000/docs`

Docker one-shot:

```bash
cd deploy
cp .env.example .env       # tweak BOOKSHELF_BIND / BOOKSHELF_DATA_DIR as needed
docker compose up -d
```

### Web UI Build

See [Web UI deployment guide](docs/web-ui.md). For production use the one-shot script (`version.json` + `rsync --delete`, excludes `skills/`):

```bash
bash scripts/deploy_frontend.sh
bash scripts/deploy_frontend.sh --base /home-bookshelf/
```

See [Path Alias Deployment](docs/web-ui.md#路径别名部署path-alias). The stock `docker compose` image now builds the frontend in Docker; no manual copy is required.

### CLI Commands (`bookshelf`)

| Command | Description |
| --- | --- |
| `add` | Intake (ISBN / image / title+author) |
| `find` / `show` | Search / detail |
| `recognize` | Recognize ISBN from image |
| `progress` | Update reading progress (5 states/page/percent/rating) |
| `purchase` | Record purchase (`--original-price` for list price) |
| `note` / `reading-log` | Reading note / daily reading log |
| `stats` | Collection & reading stats |
| `member` | Create a family member (`--name` / `--role`) |
| `doctor` | Setup diagnostics (API/DB/Key/member binding) |
| `bind` | Bind IM channel account to a member (whitelist; empty-library first `--member-id 1` auto-creates default owner) |
| `health` | API status |
| `bootstrap` | Discover the system contract (manifest / skills index / public-health, no auth) |
| `auth status` | Check current Agent authorization status (needs `BOOKSHELF_TOKEN`) |

### Skills (Agent)

The `skills/` directory ships 9 skills: `book-intake` · `book-query` · `bookshelf-bootstrap` · `bookshelf-setup` · `cover-eval` · `note-taker` · `purchase-logger` · `reading-tracker` · `shelf-report`. Add the directory to your Agent's (OpenClaw / Hermes) skill path to use them.

### Agent Guide

1. Deploy the backend (above) and run `bookshelf doctor` until all checks pass
2. Install the CLI: `pip install -e cli`
3. Point it at the backend: `export BOOKSHELF_API_URL=http://<home-server-ip>:8000`
4. (Optional) Create a member: `bookshelf member --name "You" --role owner`
5. Bind a member (whitelist): `bookshelf bind --member-id 1 --channel feishu --external-user-id <channel-user-id>` (empty-library first bind with `member_id=1` auto-creates a default owner)
6. Add `skills/` to your Agent's skill path, then manage books via natural language

> ⚠️ **Security**: all business endpoints (reads and writes) enforce unified auth (AuthContext); unauthenticated requests get 401. Pick one of: Agent Bearer Token (`Authorization: Bearer ...`, scope-checked per grant — see the matrix at `design/plans/agent-authorization-matrix.md`), Web session cookie (owner password login, set from the frontend "Agent" page), or channel headers `X-Channel` / `X-External-User-Id` (must be bound to a member; optionally enable HMAC signing via `CHANNEL_SIGNING_SECRET`, passed through by the CLI as `BOOKSHELF_CHANNEL_SIGNING_SECRET`). The `X-UI-Client` header no longer carries any authorization meaning. A web owner session may act for family members; agent/channel identities are limited to their bound member. **v0.3.5 probe breaking change:** `GET /api/v1/health` requires `members:read` (401 without credentials). Use `curl -f http://<host>:<port>/api/v1/public-health` for monitors and lwa probes (the Docker healthcheck already switched). During bootstrap (no channel bindings yet) only `POST /members` and `bind` accept anonymous initialization. Still recommended to **run only on a trusted home LAN; do not expose to the public internet**. Since permission stage 1 an **anonymous shared bookshelf (mode C)** is available: with `ANONYMOUS_CATALOG_MODE=lan_shared` and `TRUSTED_LAN_CIDRS` set, LAN visitors can browse a sanitized catalog at `/shared` (see `docs/deployment.md`); untrusted sources are automatically downgraded to the login entry. Since permission stage 4 per-book visibility levels are supported (owner sets them on the detail/policy pages; in explicit_public mode only books marked public are anonymously visible). Since permission stage 2 the owner and family members sign in at `/login` with their own username + password (member accounts are created/disabled/reset by the owner; sessions are revoked immediately on role or password changes). A **default-off MCP read-only pilot** is also available (`/mcp`, two catalog read tools; requires a dedicated pilot grant and an independent cursor secret — see `docs/deployment.md`); real family data stays disabled until the official SDK and target clients are verified.
