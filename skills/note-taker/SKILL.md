---
name: note-taker
description: 读书笔记技能。当用户说「记一段摘录」「写点感想」「给这本书做个笔记」时使用。调用 bookshelf note 写入 reading_notes。
---

# 读书笔记（note-taker）

## 适用场景

- 「给《三体》记一段摘录：黑暗森林…」
- 「帮我写个读后感，5 星那本」
- 「第 88 页这句话很好，记下来」

## 前置检查

```bash
bookshelf health
```

## 信息收集

| 字段 | 必填 | 说明 |
|------|------|------|
| book_id | 是 | 书籍 ID；用户只说书名时需先 `find` |
| content | 是 | 笔记正文（Markdown） |
| note_type | 否 | `excerpt` / `review` / `thought`，默认摘录 |
| page / chapter | 否 | 页码或章节 |

## 执行步骤

1. 若只有书名 → `bookshelf find --keyword "..."` 获取 `book_id`
2. 写入笔记：

```bash
bookshelf note --book-id <ID> --content "..." [--type excerpt|review|thought] [--page N] [--chapter "第一章"]
```

## 回复规范

- 确认已为哪本书添加笔记
- 摘录类回复可复述首句供用户核对

## 异常处理

| 情况 | 处理 |
|------|------|
| 找不到书 | 提示先入库或提供更准确书名 |
| 多本同名 | 列出候选让用户选择 |
