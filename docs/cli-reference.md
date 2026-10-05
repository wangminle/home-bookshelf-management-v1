# CLI 参考

命令入口：`bookshelf`。全局环境变量：

| 变量 | 作用 |
| --- | --- |
| `BOOKSHELF_API_URL` | API 根地址，默认 `http://127.0.0.1:8000` |
| `BOOKSHELF_TOKEN` | Agent Bearer Token，CLI 全部请求（含 find/show/stats 等读命令）自动附带；签发见 `docs/agent-setup.md` |
| `BOOKSHELF_SETUP_TOKEN` / `SETUP_TOKEN` | CLI 全部请求都会自动透传为 `X-Setup-Token`；主要用于白名单建立后的 `bind` |
| `BOOKSHELF_CHANNEL` | CLI 全部请求都会自动透传为 `X-Channel` |
| `BOOKSHELF_EXTERNAL_USER_ID` | CLI 全部请求都会自动透传为 `X-External-User-Id` |
| `BOOKSHELF_CHANNEL_SIGNING_SECRET` | 可选；配置后渠道头自动附带 `X-Channel-Signature`（HMAC-SHA256），与后端 `CHANNEL_SIGNING_SECRET` 配合使用。未设置时回退读取 `CHANNEL_SIGNING_SECRET` |

多数命令支持 `--json` / `--no-json`（默认 JSON）。

若同时设置了 `BOOKSHELF_CHANNEL` 和 `BOOKSHELF_EXTERNAL_USER_ID`，CLI 写命令会按该绑定身份访问后端；只设置其中一个会被后端判为畸形请求并返回 `400`。

---

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `add` | 入库（ISBN / 图片 / 书名） |
| `find` | 搜索 |
| `show` | 详情 |
| `recognize` | 图片识别 ISBN |
| `progress` | 更新阅读进度 |
| `reading-log` | 每日阅读日志 |
| `purchase` | 购买记录 |
| `note` | 读书笔记 |
| `stats` | 统计 |
| `member` | 新建成员 |
| `bind` | 绑定 IM 渠道 |
| `doctor` | 初始化诊断 |
| `health` | API 健康检查 |
| `bootstrap` | 发现系统契约（manifest / Skills 索引 / public-health，无需认证） |
| `auth status` | 检查当前 Agent 授权状态（需 `BOOKSHELF_TOKEN`） |

---

## `add`

```bash
bookshelf add [--isbn ISBN] [--title 书名] [--author 作者] [--authors 作者]...
              [--image 路径] [--price 价格] [--channel 渠道] [--location 位置] [--member-id ID]
```

至少提供 ISBN、图片或书名之一。`--image` 支持 `~` 前缀（自动展开为家目录）。
`--authors` 可重复传入（`--authors 甲 --authors 乙`），多作者完整保留。

## `find` / `show`

```bash
bookshelf find [--keyword 词] [--author 作者] [--isbn ISBN]
bookshelf show --id ID
```

## `progress`

```bash
bookshelf progress --book-id ID [--member-id ID] [--status 状态]
                   [--page 页] [--percent 百分比] [--rating 1-5]
                   [--to-read/--no-to-read]
```

## `purchase`

```bash
bookshelf purchase --book-id ID --price 价格
                   [--original-price 定价] [--channel 渠道]
                   [--order-no 单号] [--date YYYY-MM-DD] [--notes 备注]
                   [--member-id ID]
```

## `note`

```bash
bookshelf note --book-id ID --content Markdown
               [--type excerpt|thought|review] [--page 页]
               [--chapter 章节] [--member-id ID]
```

## `reading-log`

```bash
bookshelf reading-log --book-id ID --date YYYY-MM-DD
                      [--pages N] [--minutes N] [--member-id ID] [--notes 文本]
```

## `member` / `bind`

```bash
bookshelf member --name 名称 [--role owner|member] [--avatar 路径]
bookshelf bind --member-id ID --channel 渠道名 --external-user-id 外部ID
```

## `doctor` / `health` / `recognize` / `stats` / `bootstrap` / `auth status`

```bash
bookshelf doctor [--authorized]
bookshelf health
bookshelf recognize --image 路径        # 支持 ~ 前缀
bookshelf stats
bookshelf bootstrap http://<服务器>
bookshelf auth status
```

补充：

- `bookshelf doctor` 在检查未通过时会以退出码 `1` 结束，便于 Agent / 脚本判断失败
- `doctor --authorized`：授权后业务检查，需先设置 `BOOKSHELF_TOKEN`（会先跑
  `auth status` 再做常规诊断）。JSON 模式输出**单一** JSON 文档——auth 结果并入
  顶层 `auth_status` 键，stdout 始终可整体解析；auth 失败（无效 Token / 连接失败）
  时输出失败文档并退出码 1，不再继续诊断
- `bookshelf health` / 其他命令若遇到 API 非 JSON、网络错误或 HTTP 4xx/5xx，
  会返回更明确的中文错误而不是裸 traceback：JSON 模式输出
  `{"ok": false, "error": "..."}` 单文档、文本模式向 stderr 输出一行错误，
  均以退出码 `1` 结束
- 写命令（`add`/`purchase`/`note`/`progress`/`reading-log`）报「回执丢失（可能已提交）」
  （`ApiOutcomeUnknownError`/`ApiTimeoutError`，见 `cli/bookshelf/client.py`）时——
  覆盖连接建立后的读/写超时、post-connect 读写/协议错误、2xx 响应体不可解析、
  以及写请求的任意 5xx——**请求可能已被服务端提交**，不要盲目重试：先用
  `find`/`show` 或批量脚本的 `reconcile` 核对服务端真实状态再决定。只有
  「确定未建立连接」的连接失败/连接超时（DNS、拒连等）才可安全重试
- `GET /api/v1/health` 已要求认证（`members:read`）：无凭证时 `health`/`doctor` 自动回退 `GET /api/v1/public-health` 验证可达性，并以警告提示诊断细节不可用（数据库状态显示「未知」而非误报异常）。监控脚本请直接打 `public-health`，不要再对 `/health` 期望 200。数据库断开时 `/health` 返回 503 + 诊断体，`doctor` 会正确报告「数据库未连接」并给出处置建议（检查 `DATABASE_URL` / 迁移），不会误诊为 API 不可达
- `doctor` 会读取 health 响应（无凭证走 `public-health`，持 Token 走 `/health`，两者均携带）的 `frontend_version` / `app_version`，不一致时警告 static 漂移

---

## 尚未提供的 CLI（请用 API 或 Web UI）

- `list --shelf …`、`find --status …`
- `attach` / `field`（请用 `POST /api/v1/attachments`、`POST /api/v1/custom-fields`）
- `stats --by` / `--spending` / `--year`（API 已返回 `by_year` 年度聚合，CLI 不接受过滤参数；Web UI 统计页可查看年度趋势）
- `--member` 按姓名（目前仅 `--member-id`）

设计背景见 [`design/`](../design/)。
