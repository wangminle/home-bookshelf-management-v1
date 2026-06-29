---
name: book-query
description: 家庭藏书查询技能。当用户问「有没有 XX 书」「我家关于历史的书」「这本书详情」时使用。调用 bookshelf find / show 检索书架。
---

# 藏书查询（book-query）

## 适用场景

- 「我有没有《三体》？」
- 「查一下刘慈欣的书」
- 「ISBN 9787506365437 在不在架上？」
- 「ID 12 那本书详情是什么？」
- 「我家关于 Python 的书有哪些？」

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
| API 不可用 | 提示检查后端服务 |

## 与其他技能协作

- 用户说「没有的话帮我买/入库」→ 转 **book-intake**
- 用户说「这本读到 100 页」→ 先 find 拿 book_id，再转 **reading-tracker**
