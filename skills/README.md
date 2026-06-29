# 家庭图书管理 Skills

Agent 能力层：每个 Skill 描述**何时触发、如何调用 CLI、如何回复用户**。

> **首次使用请先加载 [bookshelf-setup](./bookshelf-setup/SKILL.md)**，跑通 `bookshelf doctor` 后再用业务技能。

## 技能清单

| Skill | 目录 | 触发示例 |
|-------|------|----------|
| **书架初始化** | [bookshelf-setup](./bookshelf-setup/SKILL.md) | 「第一次用 / 怎么配置 / setup / 连不上」 |
| 藏书入库 | [book-intake](./book-intake/SKILL.md) | 发书封照片 / ISBN / 「买了本书」 |
| 藏书查询 | [book-query](./book-query/SKILL.md) | 「有没有三体」/ 「查刘慈欣」 |
| 阅读进度 | [reading-tracker](./reading-tracker/SKILL.md) | 「读到 100 页」/ 「读完了」 |
| 购书记录 | [purchase-logger](./purchase-logger/SKILL.md) | 「38 块当当买的」 |
| 读书笔记 | [note-taker](./note-taker/SKILL.md) | 「记一段摘录」/ 「写点感想」 |
| 藏书统计 | [shelf-report](./shelf-report/SKILL.md) | 「有多少书」/ 「花了多少钱」 |

## Agent 编排原则

0. **首次 setup**：`bookshelf doctor` 确认链路；有问题走 bookshelf-setup
1. **先 health**：`bookshelf health` 确认后端在线
2. **先查后改**：更新进度/购买前，若只有书名则 `find` 拿 `book_id`
3. **默认 JSON**：所有 CLI 命令保持 `--json`，便于解析 `data.message`
4. **用户确认**：入库、消歧、识别存疑时先确认再执行
5. **单一职责**：入库用 book-intake，查询用 book-query，不要混用命令

## 本地模拟对话

在 Cursor / OpenClaw / Hermes 等 Agent 中，将本目录 `skills/*/SKILL.md` 加入可用技能，CLI 指向家庭服务器：

```
用户：帮我把 9787506365437 入库，38 块当当买的
Agent：→ book-intake → bookshelf add --isbn ... --price 38 --channel 当当

用户：我有没有三体？
Agent：→ book-query → bookshelf find --keyword 三体

用户：活着读到 50 页了
Agent：→ book-query find → reading-tracker progress --book-id N --page 50
```

## 环境变量

```bash
export BOOKSHELF_API_URL=http://127.0.0.1:8000   # 家庭服务器地址
```

## CLI 命令速查

```bash
bookshelf doctor          # 首次初始化诊断（推荐）
bookshelf health
bookshelf bind --member-id 1 --channel feishu --external-user-id ou_xxx
bookshelf add --isbn ... [--price ... --channel ...]
bookshelf add --image ...
bookshelf add --title ... --author ...
bookshelf find --keyword ... [--author ...]
bookshelf show --id ...
bookshelf progress --book-id ... [--page ... --status ... --rating ...]
bookshelf reading-log --book-id ... --date YYYY-MM-DD [--pages ... --minutes ...]
bookshelf purchase --book-id ... --price ... [--channel ...]
bookshelf note --book-id ... --content "..."
bookshelf stats
bookshelf recognize --image ...
```
