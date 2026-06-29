---
name: purchase-logger
description: 购书记录技能。当用户说「这本花了 X 元」「在京东买的」「补录购买信息」时使用。调用 bookshelf purchase 写入购买记录。
---

# 购书记录（purchase-logger）

## 适用场景

- 「《活着》38 块当当买的」
- 「ID 12 这本书 45 元，京东」
- 「补录一下购买记录，订单号 xxx」
- 入库时已带 `--price` 则无需重复（intake 会自动记购买）

## 前置检查

```bash
bookshelf health
```

## 信息收集

| 字段 | 必填 | 说明 |
|------|------|------|
| book_id | 是 | 通过 find 或用户提供的 ID |
| price | 是 | 购买价格（元） |
| channel | 推荐 | 当当 / 京东 / 孔夫子 / 线下书店 等 |
| order_no | 可选 | 订单号 |
| purchase_date | 可选 | 默认今天 |

## 执行步骤

### 1. 确认书籍 ID

```bash
bookshelf find --keyword "活着"
```

### 2. 记录购买

```bash
bookshelf purchase --book-id 12 --price 38 --channel 当当
bookshelf purchase --book-id 12 --price 45 --channel 京东 --order-no JD20260101001
bookshelf purchase --book-id 12 --price 30 --date 2026-01-15 --notes 二手九成新
```

## 与入库技能的区别

| 场景 | 用哪个 |
|------|--------|
| 新书第一次入库 + 购买信息 | `bookshelf add ... --price --channel`（book-intake） |
| 书已在架，补录/追加购买 | `bookshelf purchase`（本技能） |
| 重复入库提示已存在 | 用 purchase 补购买，不要重复 add |

## 回复规范

> 已为《{title}》记录购买：¥{price}（{channel}）。

有订单号时附上：

> 订单号：{order_no}

## 输出解析

```json
{
  "ok": true,
  "data": {
    "id": 5,
    "book_id": 12,
    "price": 38.0,
    "channel": "当当",
    "message": "已为《活着》记录购买：¥38.0（当当）"
  }
}
```

## 异常处理

| 情况 | 处理 |
|------|------|
| 找不到书 | 先 find 或引导 book-intake |
| 缺少价格 | 追问「多少钱买的？」 |
| 价格 ≤ 0 | CLI/API 会拒绝，提示重新输入 |
| 用户说「免费/赠送」 | 可用 `--price 0.01` 并 `--notes 赠送` 或仅 notes（需 price>0，最低 0.01） |

## 与其他技能协作

- 用户说「买新书入库」→ **book-intake**（add 带 price）
- 用户问「这本书多少钱买的」→ **book-query** show 详情（后续可扩展购买列表）
