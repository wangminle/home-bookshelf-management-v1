---
name: book-query
description: 家庭藏书查询技能。当用户问「有没有 XX 书」「我家关于历史的书」「这本书详情」「这本书放在哪」时使用。调用 bookshelf find / show / locate 检索书架与只读定位。
scopes: [books:read]
version: "0.2.6"
---

# 藏书查询（book-query）

## 适用场景

- 「我有没有《三体》？」
- 「查一下刘慈欣的书」
- 「ISBN 9787506365437 在不在架上？」
- 「ID 12 那本书详情是什么？」
- 「我家关于 Python 的书有哪些？」
- 「《影响世界的专利》放在哪个书架？现在在架上吗？」（只读定位，见下文）

## 前置检查

```bash
bookshelf health
```

## 信息收集

| 用户提供 | 使用命令 |
|----------|----------|
| 书名关键词 | `find --keyword` |
| 作者 | `find --author` |
| ISBN | `find --isbn` |
| 书籍 ID | `show --id` |

可同时组合 keyword + author 缩小范围。

> **范围说明**：`find` 仅支持 `--keyword` / `--author` / `--isbn` 三个过滤参数；不支持按阅读状态、副本位置、分类等过滤（`--status` / `--shelf` / `--category` 等为设计方案中的二期示意命令）。

若用户描述模糊（「那本红封面的」），先追问书名/作者/ISBN，不要瞎猜。

## 执行步骤

### 1. 关键词搜索

```bash
bookshelf find --keyword "三体"
bookshelf find --keyword "历史"
bookshelf find --author "刘慈欣"
bookshelf find --isbn 9787506365437
```

### 2. 查看详情

```bash
bookshelf show --id 12
```

### 3. 多结果消歧

`find` 返回 `total > 1` 时，列出前 5 条让用户选：

```
找到 3 本，请确认是哪一本：
1. [12] 《三体》— 刘慈欣
2. [15] 《三体·黑暗森林》— 刘慈欣
...
```

用户选定后再 `show --id {id}`。

### 4. 只读定位（可选能力，需 `locations:read`）

用户问「放在哪」「在不在架」时，先 `find` 拿 book_id，再：

```bash
bookshelf locate --id 12            # 每个副本的归属位置、状态、是否在架，附一句可转述的 summary
bookshelf storage rooms             # 只读浏览：房间 / shelves / shelf --id / cell --id / unlocated
```

读取结构化位置必须由 Owner 在「Agent 授权」页**显式**授予 `locations:read`（不会自动加到已有 Token；书架外观照片另需 `files:read`）。没有该 Scope 时 `locate` 返回 `location_access: "not_granted"`，只能如实告知“未授权读取精确位置”，**不得**根据旧文字字段、书名或经验猜测房间、书架或格子。

定位回复必须遵守：

- **外借/遗失/损坏等非在架状态**：只说“归属位置为……，状态为外借，因此当前不在架”，不要说“在书架上”。
- **没有副本**：说明该书目尚无副本登记；**没有位置**：说明“未登记位置”。不推断。
- **只有旧文字位置**（`position_state = legacy_text`）：转述为“旧文字位置：……，尚未登记结构化位置”。
- **电子副本**没有实体位置，不计入实体册数。
- 同一本书多册时分别说明每一册，不合并成一个位置。
- 这是**只读**能力：不能移动、分配或清除位置（`bookshelf add --location` 仍只是原样文字，不会被拆成书架/格子）；需要改位置请让 Owner 在 Web「书架」页操作。

## 回复规范

**找到 1 本：**

> 有的！《{title}》（ID {id}），{authors}。ISBN：{isbn13}。

**找到多本：**

> 找到 {total} 本相关藏书：[简要列表]。要看哪一本的详情？

**未找到：**

> 书架上还没有「{keyword}」相关的书。需要我帮你入库吗？

**查看详情：**

> 《{title}》  
> 作者：{authors}  
> 出版社：{publisher} · {publish_date}  
> ISBN：{isbn13}  
> 分类：{category}

## 输出解析

CLI 默认 `--json`，关注字段：

```json
{
  "ok": true,
  "data": {
    "items": [...],
    "total": 3
  }
}
```

或单本 `show` 的 `data` 对象。

## 异常处理

| 情况 | 处理 |
|------|------|
| total = 0 | 明确告知没有，可引导 book-intake |
| 404 show | ID 不存在，建议重新 find |
| `locate`/`storage` 返回 403 | 当前 Token 缺少 `locations:read`，告知用户需 Owner 授权；不要改用猜测 |
| API 不可用 | 提示检查后端服务 |

## 与其他技能协作

- 用户说「没有的话帮我买/入库」→ 转 **book-intake**
- 用户说「这本读到 100 页」→ 先 find 拿 book_id，再转 **reading-tracker**
