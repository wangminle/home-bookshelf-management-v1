# 家庭图书管理 Skills

Agent 能力层：每个 Skill 描述**何时触发、如何调用 CLI、如何回复用户**。

> **首次使用请先加载 [bookshelf-setup](./bookshelf-setup/SKILL.md)**，跑通 `bookshelf doctor` 后再用业务技能。

## 技能清单

| Skill | 目录 | 触发示例 |
|-------|------|----------|
| **书架初始化** | [bookshelf-setup](./bookshelf-setup/SKILL.md) | 「第一次用 / 怎么配置 / setup / 连不上」 |
| Agent 引导 | [bookshelf-bootstrap](./bookshelf-bootstrap/SKILL.md) | 「连接书架 / bootstrap / agent 接入 / 发现能力」 |
| 藏书入库 | [book-intake](./book-intake/SKILL.md) | 发书封照片 / ISBN / 「买了本书」 |
| 藏书查询 | [book-query](./book-query/SKILL.md) | 「有没有三体」/ 「查刘慈欣」 |
| 阅读进度 | [reading-tracker](./reading-tracker/SKILL.md) | 「读到 100 页」/ 「读完了」 |
| 购书记录 | [purchase-logger](./purchase-logger/SKILL.md) | 「38 块当当买的」 |
| 读书笔记 | [note-taker](./note-taker/SKILL.md) | 「记一段摘录」/ 「写点感想」 |
| 藏书统计 | [shelf-report](./shelf-report/SKILL.md) | 「有多少书」/ 「花了多少钱」 |
| 封面识书评测 | [cover-eval](./cover-eval/SKILL.md) | 「评测视觉模型」/ 「封面 eval 达标吗」 |

## Agent 编排原则

0. **首次 setup**：`bookshelf doctor` 确认链路；有问题走 bookshelf-setup
1. **先 health**：`bookshelf health` 确认后端在线
2. **先查后改**：更新进度/购买前，若只有书名则 `find` 拿 `book_id`
3. **默认 JSON**：所有 CLI 命令保持 `--json`，便于解析 `data.message`
4. **用户确认**：入库、消歧、识别存疑时先确认再执行
5. **单一职责**：入库用 book-intake，查询用 book-query，评测视觉模型用 cover-eval，不要混用命令
6. **统一鉴权**：业务端点（读+写）统一走 AuthContext 鉴权：Agent Bearer Token（按 Grant scope 校验）/ Web 会话 / 已绑定渠道头三选一，无凭证 401；CLI 设置 BOOKSHELF_TOKEN 或 BOOKSHELF_CHANNEL/BOOKSHELF_EXTERNAL_USER_ID 后自动注入，不必手工拼头；可选 CHANNEL_SIGNING_SECRET 开启渠道头 HMAC 签名

## 本地模拟对话

在 Cursor / OpenClaw / Hermes 等 Agent 中，将本目录 `skills/*/SKILL.md` 加入可用技能，CLI 指向家庭服务器：

```
用户：帮我把 9787506365437 入库，38 块当当买的
Agent：→ book-intake → bookshelf add --isbn ... --price 38 --channel 当当

用户：我有没有三体？
Agent：→ book-query → bookshelf find --keyword 三体

用户：活着读到 50 页了
Agent：→ book-query find → reading-tracker progress --book-id N --page 50

用户：评测一下当前视觉模型能不能识书封
Agent：→ cover-eval → 看 tests/eval/covers → 填 predictions.json → python3 scripts/eval_cover_recognition.py compare
```

## 环境变量

```bash
export BOOKSHELF_API_URL=http://127.0.0.1:8000   # 家庭服务器地址
export BOOKSHELF_TOKEN=hbs_at_...                # 推荐首选：Agent Bearer Token（在「Agent 授权」页签发）
export BOOKSHELF_CHANNEL=feishu                  # 可选：按绑定成员身份执行写操作
export BOOKSHELF_EXTERNAL_USER_ID=ou_xxx         # 可选：与 bind 时一致
export BOOKSHELF_SETUP_TOKEN=...                 # 可选：白名单建立后代绑成员
export BOOKSHELF_CHANNEL_SIGNING_SECRET=...      # 可选：渠道头 HMAC 签名（与后端 CHANNEL_SIGNING_SECRET 配合）
```

## CLI 命令速查

```bash
bookshelf doctor          # 首次初始化诊断（推荐）
bookshelf health
bookshelf member --name "你" --role owner          # 新建家庭成员（需要多成员时）
bookshelf bind --member-id 1 --channel feishu --external-user-id ou_xxx   # 空库首次 member_id=1 会自动创建默认 owner
bookshelf add --isbn ... [--price ... --channel ...]
bookshelf add --image ...
bookshelf add --title ... --author ...
bookshelf find --keyword ... [--author ...]
bookshelf show --id ...
bookshelf progress --book-id ... [--page ... --status ... --rating ...]
bookshelf reading-log --book-id ... --date YYYY-MM-DD [--pages ... --minutes ...]
bookshelf purchase --book-id ... --price ... [--original-price ... --channel ...]
bookshelf note --book-id ... --content "..."
bookshelf stats
bookshelf recognize --image ...
```
