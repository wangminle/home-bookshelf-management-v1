---
name: shelf-report
description: 藏书统计技能。当用户问「我家有多少书」「今年买书花了多少」「阅读 streak」时使用。调用 bookshelf stats。
scopes: [stats:read, books:read]
version: "0.2.5"
---

# 藏书统计（shelf-report）

## 适用场景

- 「我家一共有多少书？」
- 「买书总共花了多少钱？」
- 「各分类有多少本？」
- 「谁在读几本书 / 连续阅读几天了？」

## 前置检查

```bash
bookshelf health
```

## 执行步骤

```bash
bookshelf stats
```

> **CLI 不接受过滤参数**（不支持 `--year` / `--by` / `--spending`）。API 返回全库聚合 + 年度趋势数据。

返回字段包括：

- `total_books`：藏书总数
- `by_status`：各阅读状态数量（unread/reading/finished/abandoned/dropped）
- `by_category`：分类统计
- `total_spent` / `purchase_count`：购书花费
- `reading_logs_pages_total`：阅读日志累计页数
- `members`：各成员在读/读完数量与 streak
- `by_year`：年度趋势（每年入库数/花费/阅读页数）

## 回复规范

用自然语言概括 2～4 个关键数字，避免直接 dump 整段 JSON。

示例：「目前藏书 128 本，在读 5 本；购书共花费 ¥3,240。本月阅读日志累计 420 页。」

## 异常处理

| 情况 | 处理 |
|------|------|
| API 离线 | 提示检查 `bookshelf health` 与家庭服务器 |
