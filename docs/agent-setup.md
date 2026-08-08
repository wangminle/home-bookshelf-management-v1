# 接入 Agent

一期用 **Agent（如 OpenClaw / Hermes）+ Skills + CLI** 对接飞书等 IM，不单独维护 `channels/` 适配器代码。

## 你将完成

1. 后端与 CLI 可用（见 [快速开始](./get-started.md)）  
2. 把 `skills/` 加入 Agent 技能路径  
3. 绑定家庭成员的 IM 账号  
4. 用自然语言完成一次入库或查询  

---

## 1. 准备环境

```bash
bookshelf doctor
export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000
```

Agent 运行环境需能执行 `bookshelf`，并访问上述 API。

---

## 2. 加载 Skills

仓库 `skills/` 下有 7 个技能：

| Skill | 用途 |
| --- | --- |
| `bookshelf-setup` | 部署诊断、绑定引导 |
| `book-intake` | 入库 |
| `book-query` | 查询 |
| `reading-tracker` | 进度 |
| `purchase-logger` | 购买 |
| `note-taker` | 笔记 |
| `shelf-report` | 统计 |

按你所用 Agent 的文档，把该目录加入「可用技能 / skills path」。每个目录含 `SKILL.md`，说明何时触发、如何调 CLI、如何回话。

---

## 3. 绑定成员（白名单）

```bash
bookshelf bind --member-id 1 --channel feishu --external-user-id <飞书用户ID>
```

之后 Agent 调 API 时应带上：

- `X-Channel: feishu`
- `X-External-User-Id: <同一用户ID>`

未绑定账号会收到 `403`。只传其中一个头会 `400`。

如果 Agent 直接调 HTTP，就显式带上这两个请求头；如果 Agent 通过 CLI 执行命令，直接在运行环境里设置：

```bash
export BOOKSHELF_CHANNEL=feishu
export BOOKSHELF_EXTERNAL_USER_ID=<同一用户ID>
```

CLI 现在会把这两个环境变量自动注入到所有请求；若白名单建立后还要代绑其他成员，也可额外设置：

```bash
export BOOKSHELF_SETUP_TOKEN=<管理口令>
```

---

## 4. 试一条对话

在飞书（或你的 IM）对 Bot 说：

> 帮我把《活着》余华入库  

Agent 应路由到 `book-intake` → 执行类似：

```bash
bookshelf add --title "活着" --author "余华"
```

并回复确认。再试：

> 我家有没有三体？  
> 这本读到第 100 页  

---

## 5. 常见卡点

| 现象 | 处理 |
| --- | --- |
| Agent 找不到 bookshelf | 检查 PATH / 虚拟环境；在 Agent 机器 `pip install -e cli` |
| doctor 报未绑定 | 先 `bind`；或确认 Agent 是否带了 `X-Channel`/`X-External-User-Id`，若走 CLI 则确认已设置对应环境变量 |
| 403 未绑定 | `external_user_id` 是否与 bind 时一致 |
| 识别失败 | 换清晰条码图，或改用 `--isbn` / `--title` |

更多见 [FAQ](./faq.md)。

---

## 相关

- Skills 说明总览：[`skills/README.md`](../skills/README.md)  
- 设计中的渠道与鉴权：[`design/家庭图书管理系统-设计方案.md`](../design/家庭图书管理系统-设计方案.md) §7  
