# CLI 参考

命令入口：`bookshelf`。全局环境变量：

| 变量 | 作用 |
| --- | --- |
| `BOOKSHELF_API_URL` | API 根地址，默认 `http://127.0.0.1:8000` |
| `BOOKSHELF_SETUP_TOKEN` / `SETUP_TOKEN` | CLI 全部请求都会自动透传为 `X-Setup-Token`；主要用于白名单建立后的 `bind` |
| `BOOKSHELF_CHANNEL` | CLI 全部请求都会自动透传为 `X-Channel` |
| `BOOKSHELF_EXTERNAL_USER_ID` | CLI 全部请求都会自动透传为 `X-External-User-Id` |

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

---

## `add`

```bash
bookshelf add [--isbn ISBN] [--title 书名] [--author 作者] [--image 路径]
              [--price 价格] [--channel 渠道] [--location 位置] [--member-id ID]
```

至少提供 ISBN、图片或书名之一。

## `find` / `show`

```bash
bookshelf find [--keyword 词] [--author 作者] [--isbn ISBN]
bookshelf show --id ID
```

## `progress`

```bash
bookshelf progress --book-id ID [--member-id ID] [--status 状态]
                   [--page 页] [--percent 百分比] [--rating 1-5]
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
bookshelf member --name 名称 [--role owner|member|guest] [--avatar 路径]
bookshelf bind --member-id ID --channel 渠道名 --external-user-id 外部ID
```

## `doctor` / `health` / `recognize` / `stats`

```bash
bookshelf doctor
bookshelf health
bookshelf recognize --image 路径
bookshelf stats
```

补充：

- `bookshelf doctor` 在检查未通过时会以退出码 `1` 结束，便于 Agent / 脚本判断失败
- `bookshelf health` / 其他命令若遇到 API 非 JSON、网络错误或 HTTP 4xx/5xx，会返回更明确的中文错误而不是裸 traceback

---

## 一期未提供的 CLI（请用 API 或后续版本）

- `list --shelf …`、`find --status …`
- `attach` / `field`（请用 `POST /api/v1/attachments`、`POST /api/v1/custom-fields`）
- `stats --by` / `--spending` / `--year`
- `--member` 按姓名（目前仅 `--member-id`）

设计背景见 [`design/`](../design/)。
