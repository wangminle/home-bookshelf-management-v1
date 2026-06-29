# ISBN / 书目元数据 API 调研报告

> 日期：2026-06-26  
> 背景：当前 M3 使用 OpenLibrary 作为唯一元数据源；家庭藏书以中文书为主，需评估是否有更好的 ISBN 数据库/API，以及是否应改用大模型搜索。

---

## 1. 结论先行

| 问题 | 结论 |
|---|---|
| OpenLibrary 够不够好？ | **作为免费基线很好，但对中文书明显偏弱**；不应作为唯一源 |
| 要不要换 Google Scholar？ | **不适合**。无公开 API，且面向学术论文而非 ISBN 图书编目 |
| Google Books API 值不值得加？ | **值得，强烈建议作为第二源**（免费 + API Key，中英文覆盖优于 OL） |
| 中文书怎么办？ | **国图（NLC）最权威、豆瓣最丰富**，但都没有稳定官方开放 API，需插件/爬虫/自托管方案 |
| 大模型搜索能不能替代 API？ | **不能替代 ISBN 精确查重**；适合作为「无 ISBN / 封面 OCR / 模糊匹配」的补位，且必须让用户确认 |
| 推荐架构 | **多源 Provider 链 + 用户确认**，而非单源或纯 LLM |

---

## 2. 当前方案：Open Library

**我们已在用**：`https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&jscmd=data`

### 优点
- 完全免费，**无需 API Key**
- 全球书目体量大，社区维护
- 提供 Covers API、Search API、Works/Editions 等完整生态
- jelu、mybibliotheca、calibre-web 社区广泛验证

### 缺点
- **中文书覆盖率与字段完整度弱于国图/豆瓣/Google Books（中文）**
- 数据质量依赖社区导入，存在重复/缺字段（约 36% 版本无 ISBN）
- 速率限制：默认 **1 req/s**；带 User-Agent+邮箱可升至 **3 req/s**
- Covers API 按 ISBN 访问：**100 次/IP/5 分钟**

### 适用场景
- 英文/国际 ISBN 入库的第一免费源
- 家庭服务器低频调用（完全够用）
- 作为多源链的「零成本兜底」

---

## 3. 重点候选 API 逐一评估

### 3.1 Google Books API ⭐ 推荐加入

- 文档：[Google Books API](https://developers.google.com/books/docs/v1/using)
- ISBN 查询：`GET https://www.googleapis.com/books/v1/volumes?q=isbn:{ISBN}&key={API_KEY}`
- 也支持 `intitle:` / `inauthor:` / `inpublisher:` 等字段搜索

| 维度 | 评价 |
|---|---|
| 费用 | 免费（GCP 项目 + API Key） |
| 中文覆盖 | **明显优于 OpenLibrary**（华人常用书、引进版更全） |
| 字段 | title, authors, publisher, publishedDate, pageCount, categories, description, imageLinks, industryIdentifiers |
| 限制 | 官方在 [Overview](https://developers.google.com/books/docs/overview) 明确：**不旨在替代商业服务**；配额在 GCP Console 管理，超限返回 429 |
| 第三方经验 | Apify ISBN Lookup 提到无 Key 时 Google Books 日配额约 ~1000 次/天（非官方精确数字，需以 Console 为准） |

**建议**：作为我们 `MetadataProvider` 链的 **Priority #2**（OpenLibrary 失败后立即尝试）。

---

### 3.2 Google Scholar ❌ 不推荐

- **无官方 API**，ToS 禁止自动化抓取（见 [quelle 项目说明](https://github.com/vcoeur/quelle)）
- 定位是**学术论文搜索引擎**，不是 ISBN 图书编目库
- 不提供结构化的书名/作者/出版社/ISBN 字段
- 即使用 SERP 抓取也不稳定、有合规风险

**结论**：Scholar 与家庭藏书 ISBN 入库场景**不匹配**，应排除。

---

### 3.3 ISBNdb（商业）

- 介绍：[Top 9 Book APIs 2026](https://isbndb.com/blog/book-api/)
- 数据量：1.08 亿+ 图书，19 种字段
- 费用：**付费**（Basic/Premium/Pro）
- 限速：1~5 req/s（按套餐）

| 维度 | 评价 |
|---|---|
| 优点 | 数据最全、字段最规范、适合大规模/商业 |
| 缺点 | 家庭项目成本不值；我们一期低频入库用免费源足够 |
| 建议 | **二期以后**若藏书量 >5000 且对元数据质量要求极高再考虑 |

---

### 3.4 中国国家图书馆（NLC）⭐ 中文书最佳

- 实现参考：[NLCISBNPlugin](https://github.com/DoiiarX/NLCISBNPlugin)（Calibre 插件，504 stars）
- **无官方公开 REST API**；通过国图 OPAC 检索页面抓取/解析
- 社区对比（插件作者）：中文书覆盖率 ~**98%**，支持**中图分类号**

| 维度 | 评价 |
|---|---|
| 优点 | 中文书最权威；中图法分类；ISBN 精准 |
| 缺点 | 非官方 API，页面结构变化需维护；需控制频率 |
| 建议 | **中文书第二优先源**（在 Google Books 之后或之前，可 A/B 测试） |

---

### 3.5 豆瓣读书 ⚠️ 丰富但高风险

- 官方 API **已关闭**（`api.douban.com/v2/book/isbn/:isbn` 不可用）
- 社区方案：[acdzh/douban-book-api](https://github.com/acdzh/douban-book-api) 爬虫实现
- 字段最全：评分、短评、目录、装帧、定价、标签

| 维度 | 评价 |
|---|---|
| 优点 | 中文书**社区元数据**（评分/标签/简介）无可替代 |
| 缺点 | 爬虫有**风控/封号**风险；无法作为稳定生产依赖 |
| 建议 | 仅作**可选 enrichment 源**（评分/标签），不作为主入库源；或二期手动触发 |

---

### 3.6 Hardcover.app GraphQL API

- 文档：[Hardcover API Getting Started](https://docs.hardcover.app/api/getting-started/)
- 端点：`https://api.hardcover.app/v1/graphql`
- ISBN 查询：`editions(where: {isbn_13: {_eq: "..."}})`
- 限制：**60 req/min**，API 仍处 Beta，可能变更

| 维度 | 评价 |
|---|---|
| 优点 | 英文流行书 metadata 质量高；封面、系列、评分 |
| 缺点 | 需个人 Token；中文书弱；API 不稳定（Beta） |
| 建议 | 英文阅读爱好者可选；非一期必须 |

---

### 3.7 其他值得知道的源

| 源 | 说明 | 适合我们？ |
|---|---|---|
| [WorldCat/OCLC](https://www.oclc.org/) | 全球图书馆联合目录，机构级 API | 个人项目门槛高，暂不需要 |
| [BnF SRU（法国国家图书馆）](https://api.bnf.fr/api-sru-de-bnf-catalogue-general) | 免费、法语书强 | 否 |
| [Wikidata SPARQL](https://www.wikidata.org/) | 结构化元数据、系列关系 | 二期 enrichment |
| [book-metadata-mcp](https://pypi.org/project/book-metadata-mcp/) | Google Books + OL 双源 + 评分合并 | **架构参考**，可直接借鉴其多源合并逻辑 |
| [Librario](https://github.com/pagina394/librario) | 聚合 ISBNdb + Google Books + Hardcover | 自托管参考，但依赖多个 Key |

---

## 4. 大模型搜索 vs 传统 API

### 4.1 大模型**不适合**作为主 ISBN 数据库的原因

1. **无稳定 ISBN→结构化字段映射**：LLM 可能幻觉书名/作者（[AI 编目实践](https://medium.com/digirati-ch/cataloging-books-with-llms-b35cd7fde184) 指出需规则后处理才能符合编目标准）
2. **无法保证去重**：同一 ISBN 每次回答可能略有差异
3. **成本与延迟**：每次入库调 LLM 搜索比 API 慢 10~100 倍
4. **不可审计**：家庭藏书需要可复现的数据来源（`source` 字段）

### 4.2 大模型**适合**的场景（与我们设计一致）

| 场景 | 用法 |
|---|---|
| 书封**无条码** | 多模态读封面文字 → 提取书名/作者 → 调 API 搜索（不是让 LLM 编目） |
| 用户自然语言 | 「我买了本余华的书，38 块」→ Agent 理解意图 → 调 CLI |
| 多源结果**冲突** | LLM 辅助选择最匹配的一条（如两源书名略有不同） |
| 元数据**确认** | 「识别到《活着》余华，对吗？」→ 用户确认后落库 |

### 4.3 推荐：混合架构（Hybrid）

```
输入（ISBN / 图片 / 文字）
    │
    ├─ 有 ISBN 条码 → pyzbar（确定性，不用 LLM）
    │
    ├─ 有 ISBN 数字 → Provider 链（OL → Google Books → NLC）
    │
    ├─ 仅封面图片 → VLM/OCR 提取文字 → Provider.search(title, author)
    │
    └─ 全部失败 → LLM 搜索补位（最后手段）→ 强制用户确认 → source=manual/llm
```

这与 [book-metadata-mcp](https://pypi.org/project/book-metadata-mcp/) 的「双源 + 评分 + 429 熔断」思路一致。

---

## 5. 针对我们项目的推荐 Provider 链

### 一期优化（建议立即做）

```
Priority 1: OpenLibrary     （已有，免费，国际书）
Priority 2: Google Books    （新增，需 GCP API Key，中英文）
Priority 3: 手动 / 用户确认  （兜底）
```

**预期提升**：英文书基本不变；中文书命中率从 ~50-60% 提升到 ~80-90%（经验估计，需实测）。

### 一期增强（可选，中文家庭强烈建议）

```
Priority 2.5: NLC 国图插件逻辑移植
  → 新建 NLCProvider，参考 NLCISBNPlugin 的检索与解析
  → 仅在中国 ISBN（978-7 开头）时启用
```

### 二期 enrichment（非入库必须）

```
- 豆瓣（评分/标签，可选爬虫）
- Wikidata（系列/作者关系）
- Hardcover（英文系列/封面）
```

---

## 6. 与我们现有代码的映射

当前实现（`app/services/metadata/`）已是 **Provider 接口 + 多源回退** 架构，扩展成本很低：

```python
def get_metadata_providers() -> list[MetadataProvider]:
    return [
        OpenLibraryProvider(),
        GoogleBooksProvider(),   # 新增
        NLCProvider(),           # 可选
    ]
```

ISBN 前缀路由（优化中文）：

```python
if isbn13.startswith("9787"):
    providers = [NLCProvider(), GoogleBooksProvider(), OpenLibraryProvider()]
else:
    providers = [OpenLibraryProvider(), GoogleBooksProvider()]
```

---

## 7. 行动建议

| 优先级 | 动作 | 工作量 |
|---|---|---|
| P0 | 新增 `GoogleBooksProvider`，接入现有 Provider 链 | 小（~100 行） |
| P1 | GCP 申请 Books API Key，写入 `.env` | 15 分钟 |
| P1 | 入库结果始终带 `matched_source` + 用户确认流程（已有 message，M5 强化） | 中 |
| P2 | 移植 NLC 国图 Provider（中文 ISBN 978-7） | 中 |
| P3 | 豆瓣 enrichment（评分/标签，非主源） | 中，有风险 |
| ❌ | Google Scholar / 纯 LLM 搜索替代 API | 不做 |

---

## 8. 参考链接

- [Open Library API](https://openlibrary.org/developers/api)
- [Google Books API Using Guide](https://developers.google.com/books/docs/v1/using)
- [Google Books API Overview（非商业替代说明）](https://developers.google.com/books/docs/overview)
- [ISBNdb Blog - Top 9 Book APIs 2026](https://isbndb.com/blog/book-api/)
- [NLCISBNPlugin - 国图 Calibre 插件](https://github.com/DoiiarX/NLCISBNPlugin)
- [acdzh/douban-book-api - 豆瓣爬虫 API](https://github.com/acdzh/douban-book-api)
- [Hardcover API Getting Started](https://docs.hardcover.app/api/getting-started/)
- [book-metadata-mcp - 双源合并参考实现](https://pypi.org/project/book-metadata-mcp/)
- [quelle - Google Scholar 不支持说明](https://github.com/vcoeur/quelle)
