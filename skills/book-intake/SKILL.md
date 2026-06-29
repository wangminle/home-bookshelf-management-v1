---
name: book-intake
description: 家庭藏书入库技能。当用户发送书封照片、ISBN、或说「买了本书/入库/加书」时使用。调用 bookshelf CLI 完成识别、元数据补全与落库。
---

# 藏书入库（book-intake）

## 适用场景

用户意图是**把一本书加入家庭书架**，典型输入：

- 发送书封/条码照片
- 发送 ISBN 文本（如 `9787506365437`）
- 「我买了《活着》」「帮我把这本书入库」
- 「加一本三体，刘慈欣写的，38 块当当买的」

## 前置检查

1. 确认后端可用：`bookshelf health`
2. 环境变量 `BOOKSHELF_API_URL` 默认 `http://127.0.0.1:8000`

## 信息收集

| 字段 | 是否必填 | 说明 |
|------|----------|------|
| ISBN / 图片 / 书名 | 三选一 | 至少一种识别线索 |
| 作者 | 推荐 | 仅书名时用于消歧 |
| 价格 / 渠道 | 可选 | 入库同时记购买 |
| 存放位置 | 可选 | 如「客厅书架 A」 |

缺少关键信息时追问一句，不要猜测入库：

- 只有模糊书名且无图片 → 问作者或 ISBN
- 同名多本（find 结果 >1）→ 列出候选让用户选 ID

## 执行步骤

### 1. 有图片时

```bash
# 可选：先识别 ISBN 给用户确认
bookshelf recognize --image /path/to/cover.jpg

# 入库（图片会触发条码识别 + 元数据补全）
bookshelf add --image /path/to/cover.jpg
```

### 2. 有 ISBN 时

```bash
bookshelf add --isbn 9787506365437
```

### 3. 只有书名/作者时

```bash
bookshelf add --title "活着" --author "余华"
```

### 4. 入库同时记购买

```bash
bookshelf add --isbn 9787506365437 --price 38 --channel 当当
```

## 元数据来源（Agent 无需手动选择）

系统自动按 ISBN 前缀路由：

- `978-7` 中文书：国图 NLC → Google Books → Open Library → 搜索兜底
- 其他：Google Books → Open Library → 搜索兜底

## 回复规范

成功时向用户确认：

> 已入库《{title}》，作者 {authors}，ISBN {isbn13}。  
> 元数据来源：{matched_source}。  
> 书架 ID：{book.id}

若 `already_exists: true`：

> 《{title}》已在书架中（ID {book.id}）。需要我记购买或更新位置吗？

识别到书名但元数据不全时：

> 已入库《{title}》，部分信息未能自动补全，你可以补充作者或 ISBN。

## 异常处理

| 情况 | 处理 |
|------|------|
| `recognize` 未找到条码 | 请用户补 ISBN，或描述封面文字后 `--title --author` 入库 |
| API 400 | 转述错误，提示补 ISBN/书名/更清晰照片 |
| API 503 | 后端或识别服务不可用，稍后重试 |
| 重复入库 | 告知已存在，询问是否记购买/加副本 |

## 禁止事项

- 不要跳过 CLI 直接写数据库
- 不要在用户未确认时批量入库多本
- 识别结果存疑时先确认再 `add`
