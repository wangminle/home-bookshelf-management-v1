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
│   │   ├── api/v1/       路由（books/copies/intake/progress/purchases/notes/reading-logs/attachments/custom-fields/stats/members/recognize/health）
│   │   ├── auth.py       渠道白名单鉴权
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
├── skills/               Agent 技能（7 个）
├── design/               开发与需求文档（设计方案 / Schema / 调研 / 前端评估）
├── docs/                 用户说明（get-started / user-guide / web-ui / faq …）
├── AGENTS.md / CLAUDE.md
└── task-list.md
```

### 文档入口

- **使用说明**（`docs/`）：[快速开始](docs/get-started.md) · [使用指南](docs/user-guide.md) · [CLI 参考](docs/cli-reference.md) · [部署](docs/deployment.md) · [Web UI](docs/web-ui.md) · [接入 Agent](docs/agent-setup.md) · [FAQ](docs/faq.md)
- **设计与需求**（`design/`）：[设计方案](design/家庭图书管理系统-设计方案.md) · [Schema 细化](design/数据库Schema对照与一期细化.md) · [前端评估报告](design/frontend-evaluation-report.md)

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

前端开发与构建详见 [Web UI 部署指南](docs/web-ui.md)。生产部署时，构建产物拷入 `backend/static/`，由后端 SPA fallback 同时服务 API 和前端：

```bash
cd frontend
npm install && npm run build
cp -r dist/* ../backend/static/
```

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

### Skills（Agent 技能）

`skills/` 目录提供 7 个技能：`book-intake` · `book-query` · `bookshelf-setup` · `note-taker` · `purchase-logger` · `reading-tracker` · `shelf-report`。把该目录加入 Agent（OpenClaw / Hermes）的技能路径即可调用。

### Agent 使用指南

1. 部署后端（见上），运行 `bookshelf doctor` 确认全部通过
2. 安装 CLI：`pip install -e cli`
3. 指向后端：`export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000`
4. （可选）新建成员：`bookshelf member --name "你" --role owner`
5. 绑定成员（白名单）：`bookshelf bind --member-id 1 --channel feishu --external-user-id <渠道用户ID>`（空库首次绑定 `member_id=1` 会自动创建默认 owner）
6. 将 `skills/` 加入 Agent 技能路径，即可自然语言操作藏书

> ⚠️ **安全**：业务写端点（progress/notes/reading-logs/purchases/intake）已接入渠道鉴权，读取 HTTP 头 `X-Channel`/`X-External-User-Id`：无渠道头回退默认成员（一期可信局域网兜底），有渠道头但未绑定返回 403，与 body `member_id` 不一致返回 403。仍建议**只在可信家庭局域网内运行，请勿暴露到公网**。

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
│   │   ├── api/v1/       routes (books/copies/intake/progress/purchases/notes/reading-logs/attachments/custom-fields/stats/members/recognize/health)
│   │   ├── auth.py       channel whitelist auth
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
├── skills/               Agent skills (7)
├── design/               design & requirements
├── docs/                 user guides (get-started / user-guide / web-ui / faq …)
├── AGENTS.md / CLAUDE.md
└── task-list.md
```

### Docs

- User: [get-started](docs/get-started.md) · [user guide](docs/user-guide.md) · [FAQ](docs/faq.md) · [CLI](docs/cli-reference.md) · [deploy](docs/deployment.md) · [Web UI](docs/web-ui.md) · [agent](docs/agent-setup.md)
- Design: [design方案](design/家庭图书管理系统-设计方案.md) · [frontend evaluation](design/frontend-evaluation-report.md)

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

See [Web UI deployment guide](docs/web-ui.md) for details. For production, build the frontend and copy it into `backend/static/` — the backend serves both API and SPA via a fallback route:

```bash
cd frontend
npm install && npm run build
cp -r dist/* ../backend/static/
```

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

### Skills (Agent)

The `skills/` directory ships 7 skills: `book-intake` · `book-query` · `bookshelf-setup` · `note-taker` · `purchase-logger` · `reading-tracker` · `shelf-report`. Add the directory to your Agent's (OpenClaw / Hermes) skill path to use them.

### Agent Guide

1. Deploy the backend (above) and run `bookshelf doctor` until all checks pass
2. Install the CLI: `pip install -e cli`
3. Point it at the backend: `export BOOKSHELF_API_URL=http://<home-server-ip>:8000`
4. (Optional) Create a member: `bookshelf member --name "You" --role owner`
5. Bind a member (whitelist): `bookshelf bind --member-id 1 --channel feishu --external-user-id <channel-user-id>` (empty-library first bind with `member_id=1` auto-creates a default owner)
6. Add `skills/` to your Agent's skill path, then manage books via natural language

> ⚠️ **Security**: business write endpoints (progress/notes/reading-logs/purchases/intake) enforce channel auth, reading the `X-Channel` / `X-External-User-Id` headers: no channel header falls back to the default member (trusted-LAN), an unbound channel identity is rejected with 403, and a mismatch with body `member_id` is rejected with 403. Still recommended to **run only on a trusted home LAN; do not expose to the public internet**.
