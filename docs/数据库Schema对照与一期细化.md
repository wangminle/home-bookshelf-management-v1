# 数据库 Schema 对照与一期细化

> 对照来源：mybibliotheca（v1 SQLite + v2 KuzuDB）、jelu（SQLite + Exposed ORM）
> 日期：2026-06-26
> 目的：逐字段对照参考项目，细化家庭图书管理系统一期表设计

---

## 1. 参考项目 Schema 摘要

### 1.1 mybibliotheca

**v1（SQLite，已弃用但结构清晰）** — 三表扁平模型：

| 表 | 核心字段 |
|---|---|
| `user` | username, email, password_hash, is_admin, reading_streak_offset, 隐私分享开关 |
| `book` | **user_id FK**, title, author(单字符串), isbn, cover_url, page_count, publisher, published_date, categories, language, want_to_read, finish_date, rating, review, library_only, **reading_progress(REAL)**, last_read_date |
| `reading_log` | user_id, book_id, **date**, **pages_read**, notes, session_start, session_end |

特点：每用户一套书（book 绑 user_id），元数据与阅读状态混在 book 表；**reading_log 独立记录每日阅读**。

**v2（KuzuDB 图数据库，`app/schema/master_schema.json`）** — 书与用户解耦：

| 节点/关系 | 说明 |
|---|---|
| `Book` 节点 | isbn13/10, asin, subtitle, google_books_id, openlibrary_id, series, media_type, quantity, custom_metadata… |
| `Person` 节点 | 作者/译者/编者/朗读者，多角色关系边 |
| `ReadingLog` 节点 | date, pages_read, **minutes_read**, notes |
| `User -[HAS_PERSONAL_METADATA]-> Book` | personal_notes, start_date, finish_date, personal_custom_fields |
| `Book -[STORED_AT]-> Location` | 物理存放位置 |
| `User -[HAS_CUSTOM_FIELD]-> CustomField` | book_id, field_name, field_value |

特点：**书目元数据全局共享**，个人数据在关系边上；作者归一化为 Person；支持有声书/系列/自定义字段。

### 1.2 jelu

**核心分层（SQLite + Kotlin Exposed）** — 书目与用户藏书解耦（与 mybibliotheca v2 思路一致）：

```
User ──< UserBook >── Book ──< ReadingEvent
              │            ├──< Review
              │            ├── book_authors ──> Author
              │            ├── book_translators
              │            ├── book_narrators
              │            ├── book_tags ──> Tag
              │            └── book_series ──> Series
```

| 表 | 核心字段 |
|---|---|
| `book` | title, isbn10/13, publisher, published_date, summary, page_count, image, language, original_title, **google_id, goodreads_id, amazon_id, openlibrary_id, librarything_id…** |
| `user_book` | user FK, book FK, **last_reading_event**, last_reading_event_date, notes, **is_owned, to_read, is_borrowed**, percent_read, current_page_number, **price_in_cents** |
| `reading_event` | user_book FK, **event_type(FINISHED/DROPPED/CURRENTLY_READING)**, start_date, end_date |
| `review` | user, book, text, rating, visibility |
| `author` | name, biography, wikipedia_page… |
| `tag` / `book_tags` | 标签多对多 |
| `series` / `book_series` | 系列与卷号 |

特点：**book 全局唯一**（多人共享元数据）；**user_book 承载个人状态+价格**；**reading_event 事件流**记录状态变迁（非每日页数日志）。

---

## 2. 设计模式对照

| 维度 | mybibliotheca v1 | mybibliotheca v2 | jelu | 我们的选择（一期） |
|---|---|---|---|---|
| 书目与用户关系 | book.user_id（每用户重复元数据） | Book 节点共享 + 关系边存个人数据 | book + user_book 分离 | **book 共享 + member 维度状态**（对齐 jelu/v2） |
| 阅读进度 | book.reading_progress 单字段 | 关系边 start/finish_date | user_book 快照 + reading_event 事件 | **reading_progress 快照** + **reading_logs 每日日志** |
| 阅读状态 | want_to_read / library_only 布尔 | 关系边 + custom_fields | event_type 枚举 + to_read 布尔 | **status 枚举** + to_read 布尔 |
| 购买/价格 | 无独立表 | custom_fields | user_book.price_in_cents | **purchase_records 独立表**（更完整） |
| 作者 | author 单字符串 | Person 节点 + 多角色边 | author 表 + 多 join 表 | **一期 JSON 数组**；二期归一化 author 表 |
| 外部源 ID | google_books_id, openlibrary_id | 同上 + opds | 7+ 外部 ID 字段 | **google/openlibrary/goodreads/asin**（一期） |
| 每日阅读 | reading_log（pages + session） | ReadingLog（+ minutes_read） | 无（只有 event） | **reading_logs**（借鉴 mybibliotheca） |
| 扩展字段 | 无 | custom_metadata + HAS_CUSTOM_FIELD | notes 5000 字 | **custom_fields(EAV) + extra(JSON)** |
| 附件 | cover_url | 图 + Location | image 路径 | **attachments 多态表** |
| 副本/实体书 | quantity 字段 | Location 关系 | is_owned / is_borrowed | **book_copies**（实体/电子/位置/借出） |

**结论**：我们一期采用 **jelu 的「书目共享 + 用户状态分离」** 为主干，叠加 **mybibliotheca 的 reading_log 每日日志**，购买信息用独立表（比 jelu 的 price_in_cents 更完整）。

---

## 3. 一期细化表设计（对照后修订版）

> **一期共 12 张表**（ORM + Alembic 与代码一致）：`members`、`books`、`book_copies`、`reading_progress`、`reading_logs`、`purchase_records`、`reading_notes`、`tags`、`book_tags`、`attachments`、`custom_fields`、`operation_logs`。

### 3.1 `members`（≈ jelu.user / mybibliotheca.user）

| 字段 | 类型 | 来源/说明 |
|---|---|---|
| id | INTEGER PK | |
| name | TEXT NOT NULL | jelu.login / myb. username |
| role | TEXT | admin/member |
| channel_bindings | TEXT(JSON) | **我们独有**：飞书/微信/Telegram 映射 |
| avatar_path | TEXT | |
| reading_streak_offset | INTEGER DEFAULT 0 | 借鉴 myb. 阅读 streak |
| created_at / updated_at | TEXT | |

> 一期不做密码认证（IM 白名单鉴权）；二期 Web 再加 login/password。

### 3.2 `books`（≈ jelu.book / myb. Book 节点）

| 字段 | 类型 | 对照 | 变更说明 |
|---|---|---|---|
| id | INTEGER PK | | |
| isbn13 | TEXT UNIQUE | 三者共有 | 保留，可空 |
| isbn10 | TEXT | jelu | 保留 |
| asin | TEXT | myb v2 | **新增** |
| title | TEXT NOT NULL | 共有 | |
| subtitle | TEXT | myb v2 | 保留 |
| original_title | TEXT | jelu | 保留 |
| normalized_title | TEXT | myb v2 | **新增**，入库时 lower/strip，辅助模糊搜索 |
| authors | TEXT(JSON) | 一期简化 | 暂 JSON 数组，二期拆 author 表 |
| translators | TEXT(JSON) | jelu book_translators | 保留 JSON |
| publisher | TEXT | 共有 | |
| publish_date | TEXT | 共有 | YYYY-MM 或 YYYY-MM-DD |
| edition | TEXT | 我们原设计 | 保留 |
| language | TEXT | 共有 | |
| page_count | INTEGER | 共有 | |
| cover_path | TEXT | jelu.image / myb.cover_url | 本地相对路径 |
| category | TEXT | myb.categories | 主分类 |
| summary | TEXT | jelu.summary / myb.description | |
| average_rating | REAL | myb v2 | **新增** |
| rating_count | INTEGER | myb v2 | **新增** |
| google_books_id | TEXT | 共有 | **新增** |
| openlibrary_id | TEXT | 共有 | **新增** |
| goodreads_id | TEXT | jelu | **新增** |
| source | TEXT | 我们原设计 | openlibrary/google/manual… |
| extra | TEXT(JSON) | myb.custom_metadata | series, raw_categories, media_type… |
| created_at / updated_at | TEXT | | |

### 3.3 `book_copies`（≈ jelu is_owned + myb Location + 我们二期预留）

| 字段 | 类型 | 对照 | 说明 |
|---|---|---|---|
| id | INTEGER PK | | |
| book_id | INTEGER FK | | |
| copy_type | TEXT | myb media_type | physical/ebook/audiobook |
| format | TEXT | | 平装/EPUB/PDF… |
| location | TEXT | myb STORED_AT | 客厅书架A-3层 |
| file_path | TEXT | | 二期电子书 |
| owner_member_id | INTEGER FK | jelu user | 可空=家庭共有 |
| acquire_type | TEXT | | purchased/gift/borrowed |
| status | TEXT | jelu is_borrowed | in_shelf/lent_out/lost/reading |
| condition | TEXT | | 品相 |
| extra | TEXT(JSON) | | 借给谁、归还日期 |
| created_at / updated_at | TEXT | | |

> 一期入库默认创建一条 `physical` 副本；jelu 的 is_owned 映射为「有副本即拥有」。

### 3.4 `reading_progress`（≈ jelu.user_book 快照 / myb HAS_PERSONAL_METADATA）

| 字段 | 类型 | 对照 | 变更说明 |
|---|---|---|---|
| id | INTEGER PK | | |
| book_id | INTEGER FK | | |
| member_id | INTEGER FK | | |
| status | TEXT | jelu event_type + myb flags | unread/reading/finished/abandoned/**dropped** |
| to_read | BOOLEAN | jelu.to_read | **新增** |
| owned | BOOLEAN | jelu.is_owned | **新增**（与副本冗余但便于查询） |
| borrowed | BOOLEAN | jelu.is_borrowed | **新增** |
| current_page | INTEGER | jelu.current_page_number | |
| percent | REAL | jelu.percent_read | |
| personal_notes | TEXT | jelu.notes / myb personal_notes | **新增**（短备注） |
| start_date | TEXT | myb start_date | |
| finish_date | TEXT | myb finish_date | |
| last_read_at | TEXT | myb last_read_date | |
| rating | INTEGER | myb.rating | 1-5 |
| extra | TEXT(JSON) | | |
| updated_at | TEXT | | |

> **UNIQUE(book_id, member_id)** — 一个成员对一本书一条进度。

### 3.5 `reading_logs`（≈ myb.reading_log / myb v2 ReadingLog）— **新增表**

| 字段 | 类型 | 对照 | 说明 |
|---|---|---|---|
| id | INTEGER PK | | |
| book_id | INTEGER FK | | |
| member_id | INTEGER FK | | |
| log_date | TEXT NOT NULL | myb.date | 阅读日期 YYYY-MM-DD |
| pages_read | INTEGER | myb.pages_read | 当日读了几页 |
| minutes_read | INTEGER | myb v2 minutes_read | 当日阅读分钟数 |
| notes | TEXT | myb.notes | |
| session_start | TEXT | myb.session_start | 可选 |
| session_end | TEXT | myb.session_end | 可选 |
| created_at | TEXT | | |

> 与 `reading_progress` 分工：**progress = 当前状态快照；logs = 历史流水**（支持 streak 统计，借鉴 mybibliotheca）。

### 3.6 `purchase_records`（我们独有，优于 jelu price_in_cents）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | |
| book_id | INTEGER FK | |
| copy_id | INTEGER FK | 可空 |
| purchase_date | TEXT | |
| price | REAL | 实付 |
| original_price | REAL | 定价 |
| currency | TEXT DEFAULT 'CNY' | |
| channel | TEXT | 当当/京东/孔夫子… |
| order_no | TEXT | |
| seller | TEXT | |
| buyer_member_id | INTEGER FK | |
| notes | TEXT | |
| extra | TEXT(JSON) | |
| created_at | TEXT | |

### 3.7 `reading_notes`（≈ jelu.review 但更轻）

| 字段 | 类型 | 对照 |
|---|---|---|
| id | INTEGER PK | |
| book_id / member_id | FK | |
| note_type | TEXT | excerpt/thought/review |
| content_md | TEXT | jelu.review.text（Markdown） |
| page / chapter | | |
| created_at / updated_at | | |

### 3.8 `tags` + `book_tags`（≈ jelu）

标准多对多，一期保留。

### 3.9 `attachments` + `custom_fields`（≈ myb v2 HAS_CUSTOM_FIELD + 我们原设计）

保持不变，作为扩展机制。

### 3.10 `operation_logs`

保持不变，便于 IM/Agent 链路调试。

---

## 4. 一期不做的表（二期预留）

| 参考项目有 | 我们二期再加 | 理由 |
|---|---|---|
| jelu.author + book_authors | `authors` + join 表 | 一期 JSON 够用，减少复杂度 |
| jelu.series + book_series | `series` + join 表 | extra JSON 暂存系列名 |
| jelu.review（独立评分表） | 可合并到 reading_notes | note_type=review 即可 |
| myb v2 Publisher/Category 节点 | category 字段 + tags | 一期够用 |

---

## 5. 关键设计决策记录

1. **为什么不用 mybibliotheca v1 的 book.user_id 模型？**  
   家庭多人可能藏同一本书，jelu 已验证「元数据共享 + 个人状态分离」更合理，避免重复录入元数据。

2. **为什么同时有 reading_progress 和 reading_logs？**  
   jelu 用 event 流（FINISHED/DROPPED），mybibliotheca 用 daily log（pages_read/minutes）。我们两者都要：progress 供 IM 快速答「读到哪」，logs 供 streak/统计（myb 核心特色）。

3. **为什么 purchase_records 不放在 reading_progress 里？**  
   同一本书可能多次购买（不同副本/版本），jelu 的 price_in_cents 只能存一个价格，独立表更灵活。

4. **为什么一期 authors 仍用 JSON？**  
   jelu 的 author 归一化适合大规模书库与作者页；家庭量级一期 JSON + 外部源补全足够，二期再拆表。

---

## 6. 与参考项目的 API/交互借鉴

| 场景 | 参考 | 我们一期做法 | 实现状态 |
|---|---|---|---|
| ISBN 入库 | myb + jelu | `POST /api/v1/books/intake` | ✅ 已实现 |
| 每日阅读 | myb reading_log | `POST /api/v1/books/{id}/reading-logs` | ✅ 已实现 |
| 状态变更 | jelu reading_event | `POST /api/v1/books/{id}/progress`（非 PATCH 子资源） | ✅ 已实现 |
| 查重 | jelu 全局 book | isbn13 唯一 + title+author 模糊 | ✅ 已实现 |
| 统计 | jelu `/api/v1/stats/{year}` | `GET /api/v1/stats`（一期基础版，无按年路径） | ✅ 已实现（基础）；按年筛选 🔶 二期 |
