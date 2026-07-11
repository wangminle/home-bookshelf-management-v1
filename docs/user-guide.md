# 使用指南

日常如何用 CLI（或经 Agent 间接）管理家庭藏书。

默认命令输出 JSON，便于 Agent 解析；人眼阅读可加 `--no-json`。

---

## 入库

```bash
bookshelf add --isbn 9787... 
bookshelf add --title "书名" --author "作者"
bookshelf add --image ./cover.jpg
bookshelf add --isbn 9787... --price 38 --channel 当当
bookshelf add --isbn 9787... --location "客厅书架A"   # 同时登记实体副本
```

要点：

- 系统会按 ISBN 与规范化书名查重；已存在会提示「已在书架」。  
- **不会默认创建副本**；只有传了 `--location` 才会登记一条实体副本。  
- ISBN 会校验校验位；错误校验位会被拒绝。  

---

## 查询

```bash
bookshelf find --keyword "三体"
bookshelf find --author "刘慈欣"
bookshelf find --isbn 9787...
bookshelf show --id 12
```

`show` 会聚合详情（含进度、购买、笔记等，视接口返回而定）。

---

## 阅读进度

```bash
bookshelf progress --book-id 12 --page 120
bookshelf progress --book-id 12 --status reading
bookshelf progress --book-id 12 --status finished --rating 5
```

状态：`unread` / `reading` / `finished` / `abandoned` / `dropped`。

可选 `--member-id` 指定家庭成员。

---

## 每日阅读日志

```bash
bookshelf reading-log --book-id 12 --date 2026-07-11 --pages 30 --minutes 45
```

用于连续阅读天数等统计。

---

## 购买记录

```bash
bookshelf purchase --book-id 12 --price 45 --original-price 55 --channel 京东
```

---

## 读书笔记

```bash
bookshelf note --book-id 12 --content "## 摘录\n……" --type excerpt --page 88
```

`--type`：`excerpt` / `thought` / `review`。

---

## 统计

```bash
bookshelf stats --no-json
```

一期为整体聚合（册数、状态、分类、花费、streak 等）。按年/按维细分统计属于后续能力。

---

## 成员与渠道

```bash
bookshelf member --name "配偶" --role member
bookshelf bind --member-id 2 --channel feishu --external-user-id ou_yyy
```

白名单建立后，匿名再绑会被拒绝；可用已绑定的 owner 身份代绑，或配置 `SETUP_TOKEN` / `BOOKSHELF_SETUP_TOKEN`。详见 [FAQ](./faq.md)。

---

## 识别与健康检查

```bash
bookshelf recognize --image ./barcode.jpg
bookshelf health
bookshelf doctor
```

---

## 下一步

- [CLI 参考](./cli-reference.md)  
- [接入 Agent](./agent-setup.md)  
- [FAQ](./faq.md)  
