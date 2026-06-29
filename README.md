# 家庭图书管理系统 V2 · Home Bookshelf Management V2

**[🇨🇳 中文](#中文)** · **[🇬🇧 English](#english)**

> 面向家庭藏书的自托管管理系统：FastAPI 后端 + Typer CLI + Agent 技能，支持 ISBN/拍照/书名入库、多源元数据聚合、阅读追踪与统计。
>
> A self-hosted home bookshelf manager: FastAPI backend + Typer CLI + Agent skills. Intake by ISBN / photo / title, multi-source metadata, reading tracking & stats.

---

## 中文

### 核心功能

- **多方式入库**：ISBN 条码 / 书封照片 / 书名+作者，自动查重（ISBN + 规范化书名）
- **多源元数据聚合**：OpenLibrary · Google Books · 国图 NLC（中文 `9787` ISBN 自动路由）
- **副本与购买记录**：多副本管理、购买价格/渠道/订单号、花费统计
- **阅读追踪**：5 态进度（想读/在读/读完/弃读/放弃）、每日阅读日志、连续天数、读书笔记
- **附件**：书籍/成员/笔记可挂链接、文件、Markdown
- **成员与 IM 绑定**：家庭成员 + 渠道白名单（飞书/Telegram 等）鉴权
- **识别与诊断**：封面/条码识别、`doctor` 自检

### 项目结构

```
home-bookshelf-management-v1/
├── backend/              FastAPI 后端
│   ├── app/
│   │   ├── api/v1/       路由（books/intake/progress/purchases/notes/attachments/stats/members/recognize/health）
│   │   ├── services/     业务逻辑（intake/metadata/reading/cover_recognition/storage…）
│   │   ├── models/       SQLAlchemy 2.0 模型
│   │   ├── schemas/      Pydantic v2 schemas
│   │   └── alembic/      数据库迁移（SQLite, WAL）
│   ├── install.sh / install.bat
│   └── requirements.txt
├── cli/                  Typer CLI（命令 bookshelf）
├── deploy/               docker-compose / systemd / backup.sh
├── skills/               Agent 技能（7 个）
├── docs/                 设计文档与调研
├── AGENTS.md / CLAUDE.md
└── task-list.md
```

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

### CLI 命令（`bookshelf`）

| 命令 | 说明 |
| --- | --- |
| `add` | 入库（ISBN / 图片 / 书名+作者） |
| `find` / `show` | 搜索 / 详情 |
| `recognize` | 识别图片中的 ISBN |
| `progress` | 更新阅读进度（5 态/页码/百分比/评分） |
| `purchase` | 记录购买信息 |
| `note` / `reading-log` | 读书笔记 / 每日阅读日志 |
| `stats` | 藏书与阅读统计 |
| `doctor` | 初始化诊断（API/DB/Key/成员绑定） |
| `bind` | 绑定 IM 渠道账号到成员（白名单） |
| `health` | 查看 API 状态 |

### Skills（Agent 技能）

`skills/` 目录提供 7 个技能：`book-intake` · `book-query` · `bookshelf-setup` · `note-taker` · `purchase-logger` · `reading-tracker` · `shelf-report`。把该目录加入 Agent（OpenClaw / Hermes）的技能路径即可调用。

### Agent 使用指南

1. 部署后端（见上），运行 `bookshelf doctor` 确认全部通过
2. 安装 CLI：`pip install -e cli`
3. 指向后端：`export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000`
4. 绑定成员（白名单）：`bookshelf bind --member-id 1 --channel feishu --external-user-id <渠道用户ID>`
5. 将 `skills/` 加入 Agent 技能路径，即可自然语言操作藏书

> ⚠️ **安全**：API 无 token 鉴权，仅靠 IM 渠道白名单识别身份——**只在可信家庭局域网内运行，请勿暴露到公网**。

---

## English

### Core Features

- **Flexible intake**: ISBN barcode / cover photo / title+author, with dedup (ISBN + normalized title)
- **Multi-source metadata**: OpenLibrary · Google Books · NLC (auto-routes Chinese `9787` ISBNs)
- **Copies & purchases**: multiple copies, price/channel/order tracking, spending stats
- **Reading tracking**: 5-state progress (unread/reading/finished/abandoned/dropped), daily logs, streaks, notes
- **Attachments**: link/file/markdown on books, members, notes
- **Members & IM binding**: family members + channel whitelist (Feishu/Telegram) for auth
- **Recognition & diagnostics**: cover/barcode recognition, `doctor` self-check

### Project Structure

```
home-bookshelf-management-v1/
├── backend/              FastAPI backend
│   ├── app/
│   │   ├── api/v1/       routes (books/intake/progress/purchases/notes/attachments/stats/members/recognize/health)
│   │   ├── services/     business logic (intake/metadata/reading/cover_recognition/storage…)
│   │   ├── models/       SQLAlchemy 2.0 models
│   │   ├── schemas/      Pydantic v2 schemas
│   │   └── alembic/      migrations (SQLite, WAL)
│   ├── install.sh / install.bat
│   └── requirements.txt
├── cli/                  Typer CLI (command: bookshelf)
├── deploy/               docker-compose / systemd / backup.sh
├── skills/               Agent skills (7)
├── docs/                 design docs & research
├── AGENTS.md / CLAUDE.md
└── task-list.md
```

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

### CLI Commands (`bookshelf`)

| Command | Description |
| --- | --- |
| `add` | Intake (ISBN / image / title+author) |
| `find` / `show` | Search / detail |
| `recognize` | Recognize ISBN from image |
| `progress` | Update reading progress (5 states/page/percent/rating) |
| `purchase` | Record purchase |
| `note` / `reading-log` | Reading note / daily reading log |
| `stats` | Collection & reading stats |
| `doctor` | Setup diagnostics (API/DB/Key/member binding) |
| `bind` | Bind IM channel account to a member (whitelist) |
| `health` | API status |

### Skills (Agent)

The `skills/` directory ships 7 skills: `book-intake` · `book-query` · `bookshelf-setup` · `note-taker` · `purchase-logger` · `reading-tracker` · `shelf-report`. Add the directory to your Agent's (OpenClaw / Hermes) skill path to use them.

### Agent Guide

1. Deploy the backend (above) and run `bookshelf doctor` until all checks pass
2. Install the CLI: `pip install -e cli`
3. Point it at the backend: `export BOOKSHELF_API_URL=http://<home-server-ip>:8000`
4. Bind a member (whitelist): `bookshelf bind --member-id 1 --channel feishu --external-user-id <channel-user-id>`
5. Add `skills/` to your Agent's skill path, then manage books via natural language

> ⚠️ **Security**: the API has no token auth — identity relies solely on the IM channel whitelist. **Run only on a trusted home LAN; do not expose to the public internet.**
