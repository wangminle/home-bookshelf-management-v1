---
name: reading-tracker
description: 阅读进度跟踪技能。当用户说「读到第 X 页」「在读」「读完了」「给这本书打 5 分」时使用。调用 bookshelf progress 更新进度。
---

# 阅读进度（reading-tracker）

## 适用场景

- 「《活着》读到 120 页了」
- 「三体读完了，5 分」
- 「我开始读 ID 12 那本书了」
- 「标记为在读」

## 前置检查

```bash
bookshelf health
```

## 信息收集

| 字段 | 必填 | 说明 |
|------|------|------|
| book_id | 是 | 书籍 ID；用户只说书名时需先 find |
| 页码 / 状态 / 评分 | 至少一项 | 决定更新什么 |

**book_id 获取流程：**

1. 用户给了 ID → 直接用
2. 用户给了书名 → `bookshelf find --keyword "..."`，消歧后取 ID
3. 多本同名 → 列出候选让用户选

可选 `--member-id`；未指定时使用系统默认成员（首个成员或自动创建的「默认用户」）。

> **渠道鉴权**（Agent 走后端时）：调用 `/progress` 端点时，后端读取 HTTP 头 `X-Channel` / `X-External-User-Id`：
> - 无渠道头 -> 回退到默认成员（一期可信局域网兜底）
> - 有渠道头但未绑定 -> 403
> - 有渠道头且绑定，但与 body `member_id` 不一致 -> 403
>
> 内置 Web UI 发送 `X-UI-Client: web` 头在白名单建立后仍可写入（BUG-134），外部 Agent/CLI 不受此旁路影响。
> 若 Agent 通过 CLI 执行命令，只需在运行环境设置 `BOOKSHELF_CHANNEL` / `BOOKSHELF_EXTERNAL_USER_ID`，CLI 会自动透传为请求头。

## 执行步骤

### 1. 更新页码

```bash
bookshelf progress --book-id 12 --page 120
```

系统自动计算百分比（若书有总页数）。

### 2. 标记在读

```bash
bookshelf progress --book-id 12 --status reading
```

### 3. 标记读完 + 评分

```bash
bookshelf progress --book-id 12 --status finished --rating 5
```

### 4. 仅更新百分比

```bash
bookshelf progress --book-id 12 --percent 45
```

## 状态枚举

| status | 含义 |
|--------|------|
| `unread` | 未读 |
| `reading` | 在读 |
| `finished` | 已读完 |
| `abandoned` | 弃读（不再继续） |
| `dropped` | 放弃（中途放弃） |

## 回复规范

> 《{title}》阅读进度已更新至第 {current_page} 页（约 {percent}%）。

读完时：

> 《{title}》已标记为读完！{rating 分时：你的评分：{rating} 星}

## 输出解析

```json
{
  "ok": true,
  "data": {
    "book_id": 12,
    "status": "reading",
    "current_page": 120,
    "percent": 48.0,
    "message": "《活着》阅读进度已更新至第 120 页"
  }
}
```

## 异常处理

| 情况 | 处理 |
|------|------|
| 找不到书（find 无结果） | 问是否要先入库 |
| book_id 不存在 | 提示重新查询 |
| 页码 > 总页数 | 自动截断为 page_count，静默记录 |
| 用户未指明哪本书 | 追问书名或 ID |
| API 403 | 渠道身份未绑定/与 member_id 不一致，提示先 `bind` 或核对成员 |

## 与其他技能协作

- 不知道哪本书 → 先用 **book-query** 的 find
- 用户顺便说购买价格 → 完成后可转 **purchase-logger**
