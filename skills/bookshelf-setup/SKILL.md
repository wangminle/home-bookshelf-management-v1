---
name: bookshelf-setup
description: 家庭书架初始化技能。当用户说「第一次使用」「初始化书架」「怎么配置」「连接不上」「setup」时使用。引导完成 API 连通、Google Books Key、Agent 对接与成员渠道绑定。
scopes: []
version: "0.2.4"
---

# 书架初始化（bookshelf-setup）

> **首次使用请先跑本技能**，完成后再使用 book-intake / book-query 等业务技能。

## 适用场景

- 「第一次用这个系统，怎么配置？」
- 「初始化书架 / setup / 连不上后端」
- 「Google Books API Key 填在哪？」
- 「飞书账号怎么绑定？」
- 「Agent 怎么接 CLI？」

## 原则

1. **不要在 IM 聊天里收集 API Key** — 引导用户在本机编辑 `deploy/.env` 或 `backend/.env`
2. **用 CLI 验证，不用猜** — 每步完成后跑 `bookshelf doctor`
3. **一次初始化，长期复用** — 完成后移交 `book-intake`

## 流程概览

```
bookshelf doctor → 修 errors → 配 Key(可选) → 配 Agent → bind 成员 → doctor 再验 → 完成
```

---

## 步骤 1：运行诊断

```bash
bookshelf doctor
```

关注 JSON 中：

| 字段 | 含义 |
|------|------|
| `checks.api_reachable` | API 是否可达 |
| `checks.db_ok` | 数据库是否正常 |
| `checks.google_books_configured` | 服务端是否配置了 Google Books Key |
| `checks.barcode_scan_available` | 服务端条码识别是否可用 |
| `checks.members_bound` | 已绑定 IM 渠道的成员数 |
| `errors` | **必须先清零** |
| `warnings` | 建议处理，不阻断基本使用 |
| `hints` | 具体操作指引 |

### 常见 errors 处理

| 现象 | 处理 |
|------|------|
| API 不可达 | 本机：`cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000`；或 `cd deploy && docker compose up -d` |
| DB 异常 | `alembic upgrade head`；检查 `data/` 目录权限 |
| `BOOKSHELF_API_URL` 错误 | Agent/CLI 机器上 `export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000` |

---

## 步骤 2：配置 Google Books API Key（推荐）

**不要在对话里让用户发送 Key。**

告知用户：

1. 打开 [Google Cloud Console](https://console.cloud.google.com/) 创建 Books API Key
2. 写入服务端环境文件：
   - Docker：`deploy/.env` → `GOOGLE_BOOKS_API_KEY=...`
   - 裸机：`backend/.env` 或 `deploy/systemd/bookshelf.env`
3. 重启 API 服务
4. 再跑 `bookshelf doctor`，确认 `google_books_configured: true`

> OpenLibrary + 国图源无需 Key，但中文书 metadata 有 Key 更稳。

---

## 步骤 3：Agent 对接 Skills

1. 将项目 **`skills/` 目录** 加入 OpenClaw / Hermes 等 Agent 的可用技能路径
2. 确保 Agent 运行环境能执行 `bookshelf` 命令（`pip install -e cli`）
3. 设置环境变量：
   ```bash
   export BOOKSHELF_API_URL=http://127.0.0.1:8000   # 或家庭服务器地址
   export BOOKSHELF_CHANNEL=feishu                  # 可选：Agent 通过 CLI 写入时复用绑定身份
   export BOOKSHELF_EXTERNAL_USER_ID=ou_xxx         # 可选：与 bind 时一致
   export BOOKSHELF_SETUP_TOKEN=...                 # 可选：白名单建立后代绑成员
   ```
4. 确认以下技能已加载：

| Skill | 用途 |
|-------|------|
| **bookshelf-setup** | 本技能（初始化） |
| book-intake | 入库 |
| book-query | 查询 |
| reading-tracker | 阅读进度 |
| purchase-logger | 购书记录 |
| note-taker | 笔记 |
| shelf-report | 统计 |

---

## 步骤 4：成员与 IM 渠道绑定

1. 查看现有成员：
   ```bash
   bookshelf doctor   # data.members 列表
   ```
   或调用 `GET /api/v1/members`

2. 默认可能有「默认用户」（ID=1）。如需区分多位家庭成员，先创建新成员：
   ```bash
   bookshelf member --name "你" --role owner     # role 可选 owner / member / guest
   ```
   > 空库首次直接 `bookshelf bind --member-id 1 ...` 会自动创建默认 owner，无需先手动建成员。

3. 绑定飞书用户（示例）：
   ```bash
   bookshelf bind --member-id 1 --channel feishu --external-user-id ou_xxxxxxxx
   ```
   - `external_user_id`：飞书开放平台用户 open_id（`ou_` 开头）
   - 获取方式：飞书机器人事件回调中的 `sender.sender_id.open_id`

4. 绑定后 `bookshelf doctor` 应显示 `members_bound >= 1`

> 绑定后业务写端点（progress/notes/reading-logs/purchases/intake，以及 attachments/copies/custom-fields 等写接口）会校验 `X-Channel`/`X-External-User-Id`，未绑定的渠道身份返回 403。内置 Web UI 通过 `X-UI-Client: web` 头在白名单建立后仍可写入（BUG-134），外部 Agent/CLI 不受此旁路影响。
> 若 Agent 是通过 CLI 执行命令，只需在运行环境设置 `BOOKSHELF_CHANNEL` / `BOOKSHELF_EXTERNAL_USER_ID`，CLI 会自动透传为请求头。配置了 `CHANNEL_SIGNING_SECRET` 时还需设置 `BOOKSHELF_CHANNEL_SIGNING_SECRET` 以生成 HMAC 签名。
> 一期不做飞书 Webhook 自动配置；Agent 侧收到消息后映射成员 ID 即可。

---

## 步骤 5：冒烟测试

全部 warnings 可接受后：

```bash
bookshelf doctor          # ready: true
bookshelf stats           # 空库也应正常
# 可选：bookshelf add --isbn 9780141439518  # 测试入库
```

## 完成话术

> 书架已初始化完成：API 连通、数据库正常{，Google Books 已配置}{，成员已绑定飞书}。
> 你现在可以发书封照片或 ISBN，我会帮你入库。

然后 **切换至 book-intake 技能** 处理后续藏书请求。

## 异常处理

| 情况 | 话术 |
|------|------|
| doctor 有 errors | 列出 errors，逐步引导，不要跳过 |
| 用户想在聊天里发 Key | 拒绝并说明安全风险，引导编辑 .env |
| 绑定 ID 不知道 | 说明从飞书事件/开放平台获取 open_id，或二期 Web 配置 |
| doctor ready 但入库失败 | 转 book-intake 排查；必要时查服务端日志 |
| 业务端点返回 403 | 渠道身份未绑定/与 member_id 不一致，提示先 `bind` 或核对成员 |

## 相关命令速查

```bash
bookshelf doctor
bookshelf health
bookshelf member --name "你" --role owner
bookshelf bind --member-id 1 --channel feishu --external-user-id ou_xxx
bookshelf stats
export BOOKSHELF_API_URL=http://<host>:8000
```
