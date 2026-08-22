# 前端技术审计报告 · 2026-08-09

> Checkpoint：2026-08-09 两轮前端审计与修复追踪快照；问题表中的完成状态保留为历史验收证据。  
> 审计工具：impeccable audit
> 审计范围：frontend/src/ 全部视图、组件、样式、路由
> 审计基准：WCAG AA、product register（家庭图书管理工具）

---

## 审计健康度评分

| # | 维度 | 评分 | 关键发现 |
|---|------|------|----------|
| 1 | 可访问性 (A11y) | 3/4 | 封面占位符白字在黄绿色相上对比度不足 3:1 |
| 2 | 性能 | 3/4 | 概览图封面串行加载，24 张图逐张 await |
| 3 | 主题化 | 3/4 | OverviewView Canvas 全部硬编码颜色，暗色模式失效 |
| 4 | 响应式设计 | 3/4 | BookDetailView 6 个 Tab 在窄屏无溢出处理 |
| 5 | 反模式 | 3/4 | `.category-bar-fill` 动画 `width` 布局属性 |
| **总计** | | **15/20** | **Good（解决薄弱维度）** |

**评级区间**：14-17 = Good，需针对薄弱维度做定向修复。

---

## 反模式判定

**通过。** 界面不会被一眼认出是 AI 生成的：

- 主色是深青绿 `#2c7a7b`（书房调性），不是 AI 默认的紫/蓝
- 无渐变文字、无毛玻璃、无编号眉标、无 hero-metric 模板
- 书架卡片网格是正确的信息架构（封面即内容），不是 icon+heading+text 模板卡
- 整体克制度符合 product register 的 Restrained 策略

一个细微 tell：`.stat-card` 同时用 `box-shadow` + `border`，形成双重轮廓。真实设计师会二选一。

---

## 问题清单（按严重程度排序）

### P1 · 严重（发布前必须修复）

#### P1-1 封面占位符文字对比度不达标

| 字段 | 值 |
|------|-----|
| **位置** | `main.css:48-49` (`--cover-sat: 62%; --cover-light: 58%`) + `BookCover.vue:41` + `BookDetailView.vue:430` |
| **类别** | 可访问性 |
| **WCAG** | 1.4.3 Contrast (Minimum), Level AA |
| **影响** | 白色文字在 `hsl(h, 62%, 58%)` 上，当色相在 40°-180° 区间（黄/绿/青）时对比度低至 1.4:1，远低于大字 3:1 最低要求。约 40% 的书籍首字落入此区间 |
| **对比度数据** | hsl(0,62%,58%)→3.4:1 ✓ / hsl(60,62%,58%)→1.4:1 ✗ / hsl(120,62%,58%)→1.8:1 ✗ / hsl(180,62%,58%)→1.7:1 ✗ / hsl(240,62%,58%)→4.2:1 ✓ |
| **修复方案** | 将 `--cover-light` 从 58% 降到 36%（暗色模式同步）。经全色相验证，36% 是白字 ≥3:1 的临界值 |
| **建议命令** | `/impeccable polish` |

#### P1-2 BookshelfView 缺少页面主标题

| 字段 | 值 |
|------|-----|
| **位置** | `BookshelfView.vue` 全文 + `StatsView.vue:36` + `OverviewView.vue:252` |
| **类别** | 可访问性 |
| **WCAG** | 1.3.1 Info and Relationships, 2.4.6 Headings and Labels |
| **影响** | BookshelfView 无 `<h1>`；StatsView 和 OverviewView 只有 `<h2>`。屏幕阅读器用户无法通过标题导航定位页面 |
| **修复方案** | BookshelfView 添加 `<h1 class="sr-only">书架</h1>`；StatsView 和 OverviewView 的 `<h2 class="page-title">` 改为 `<h1 class="page-title">` |
| **建议命令** | `/impeccable polish` |

#### P1-3 BookDetailView Tab 列表窄屏溢出

| 字段 | 值 |
|------|-----|
| **位置** | `BookDetailView.vue:208-224` (tabs template) + `main.css:416-444` (.tabs CSS) |
| **类别** | 响应式设计 |
| **影响** | 6 个 Tab（阅读进度/副本/购买/笔记/附件/自定义）在 ≤480px 屏幕上无横向滚动或换行处理，`.tabs` 容器 `display: flex` 但没有 `overflow-x: auto`，内容被挤压或溢出 |
| **修复方案** | `.tabs` 添加 `overflow-x: auto; -webkit-overflow-scrolling: touch;`；`.tab` 添加 `flex-shrink: 0; white-space: nowrap;` |
| **建议命令** | `/impeccable adapt` |

#### P1-4 交互组件缺少 `:active` 按压态

| 字段 | 值 |
|------|-----|
| **位置** | `main.css:515-537` (.btn), `main.css:423-444` (.tab), `main.css:177-197` (.nav a), `main.css:250-265` (.book-card) |
| **类别** | 交互 / 反模式 |
| **影响** | 所有交互组件只有 default/hover/disabled，缺少 `:active` 按压反馈。用户点击时没有即时视觉确认，手感"漂浮" |
| **修复方案** | 为 `.btn:active` 添加 `transform: scale(0.98)`；`.tab:active` 和 `.nav a:active` 添加背景加深；`.book-card:active` 添加 `transform: translateY(-1px) scale(0.99)` |
| **建议命令** | `/impeccable polish` |

---

### P2 · 次要（下一轮修复）

#### P2-1 OverviewView Canvas 硬编码颜色

| 字段 | 值 |
|------|-----|
| **位置** | `OverviewView.vue:69-192` |
| **类别** | 主题化 |
| **影响** | Canvas 绘制使用 `'#f7fafc'`、`'#1a202c'`、`'#718096'`、`'#2c7a7b'` 等硬编码颜色，暗色模式下导出的图片与用户界面风格不一致 |
| **修复方案** | 从 `getComputedStyle(document.documentElement).getPropertyValue('--primary')` 等读取 CSS 变量值，传入 Canvas 绘制 |
| **建议命令** | `/impeccable colorize` |

#### P2-2 OverviewView 封面图片串行加载

| 字段 | 值 |
|------|-----|
| **位置** | `OverviewView.vue:93-104` |
| **类别** | 性能 |
| **影响** | 24 张封面图片在 for 循环中逐个 `await loadImage()`，每张等待网络往返。并行加载可快 3-5 倍 |
| **修复方案** | 使用 `Promise.all` 并行加载所有封面，加载完成后统一检查 token |
| **建议命令** | `/impeccable optimize` |

#### P2-3 `.category-bar-fill` 动画 `width` 布局属性

| 字段 | 值 |
|------|-----|
| **位置** | `main.css:615-620` |
| **类别** | 性能 / 反模式 |
| **影响** | `transition: width 0.3s ease` 触发布局重排，条形图较多时可能掉帧。检测器已确认此问题 |
| **修复方案** | 改用 `transform: scaleX()` + `transform-origin: left center`，通过 CSS 变量传递比例值 |
| **建议命令** | `/impeccable optimize` |

#### P2-4 OverviewView Canvas 无可访问性

| 字段 | 值 |
|------|-----|
| **位置** | `OverviewView.vue:266` |
| **类别** | 可访问性 |
| **WCAG** | 1.1.1 Non-text Content |
| **影响** | Canvas 对屏幕阅读器完全不可见，无 `role="img"` 或 `aria-label` |
| **修复方案** | 添加 `role="img"` 和动态 `aria-label`（如"家庭书架概览图，共 N 本藏书"） |
| **建议命令** | `/impeccable polish` |

#### P2-5 概览图加载状态无 ARIA

| 字段 | 值 |
|------|-----|
| **位置** | `OverviewView.vue:254` |
| **类别** | 可访问性 |
| **影响** | 屏幕阅读器不会播报"加载中..."状态 |
| **修复方案** | 添加 `role="status" aria-live="polite"` |
| **建议命令** | `/impeccable polish` |

#### P2-6 进度表单无输入验证反馈

| 字段 | 值 |
|------|-----|
| **位置** | `BookDetailView.vue:257-284` (progress form) |
| **类别** | 交互 |
| **影响** | `current_page` 输入无范围验证，用户可输入负数或超过 `page_count` 的值 |
| **修复方案** | 添加 `min="0"` 和 `:max="book.page_count"` 属性 |
| **建议命令** | `/impeccable harden` |

#### P2-7 统计页无空数据状态

| 字段 | 值 |
|------|-----|
| **位置** | `StatsView.vue` |
| **类别** | 交互 |
| **影响** | 当 `total_books === 0` 时显示全 0 卡片和空条形图，无引导 |
| **修复方案** | 添加 `v-if="stats.total_books === 0"` 空状态提示 |
| **建议命令** | `/impeccable onboard` |

---

### P3 · 完善（有时间再修）

#### P3-1 `.stat-card` 同时使用 `box-shadow` + `border`

| 字段 | 值 |
|------|-----|
| **位置** | `main.css:558-564` |
| **类别** | 反模式 |
| **修复方案** | 去掉 `border: 1px solid var(--border)`，只保留 `box-shadow` |

#### P3-2 骨架屏动画用 `background-position`

| 字段 | 值 |
|------|-----|
| **位置** | `main.css:695-726` (skeleton shine) |
| **类别** | 性能 |
| **修复方案** | 可改用 `transform: translateX()` 伪元素 shimmer，优先级很低 |

#### P3-3 统计页条形图缺少 ARIA

| 字段 | 值 |
|------|-----|
| **位置** | `StatsView.vue:74-83` (status bars), `89-98` (category bars) |
| **类别** | 可访问性 |
| **修复方案** | 为 `.category-bar` 容器添加 `role="img" :aria-label="..."` |

#### P3-4 OverviewView Canvas 字体不统一

| 字段 | 值 |
|------|-----|
| **位置** | `OverviewView.vue:68,73,78` 等 |
| **类别** | 主题化 |
| **修复方案** | 读取 `getComputedStyle(document.body).fontFamily` 传入 Canvas |

---

## 系统性问题

1. **交互组件状态不完整**：全站按钮、链接、卡片都缺少 `:active` 按压态。组件状态体系没有系统性建立，而是逐个添加的。应定义完整的状态 token（hover/active/focus/disabled）并在所有交互组件上一致使用。

2. **Canvas 渲染脱离设计系统**：OverviewView 的 Canvas 绘制完全绕过了 CSS 变量体系，所有颜色、字体都是硬编码的。任何主题变更（包括暗色模式）都不会反映到导出的概览图上。

3. **页面标题层级缺失**：4 个视图中有 3 个没有 `<h1>`。heading hierarchy 没有作为设计规范确立。

---

## 审计中的积极发现

- **设计 token 体系完善**：`main.css` 的 `:root` 定义了完整的语义 token，包括暗色模式覆盖，且所有视图都使用 `var(--...)` 引用
- **骨架屏**：使用 shimmer 骨架屏而非 spinner，符合 product register 要求
- **ARIA tablist**：BookDetailView 的 Tab 实现了完整的 `role="tablist"` / `role="tab"` / `role="tabpanel"` + 键盘导航（ArrowLeft/Right/Home/End），教科书级
- **触控目标**：全站交互元素 `min-height: var(--tap-min)` (44px)，移动端友好
- **懒加载**：书封面 `loading="lazy"`，路由 `() => import(...)` 懒加载
- **减弱动画**：全局 `@media (prefers-reduced-motion: reduce)` 正确实现
- **竞态防护**：`useBooks.ts` 请求序号 + `OverviewView.vue` 生成 token 都正确实现了过期响应丢弃

---

## 审美提升方案

除技术修复外，以下是可以提升界面品质的审美方向（可在技术修复后作为后续迭代）：

1. **统计卡片色彩区分**：当前 5 个 stat-card 全部用 `--primary`。可按语义着色--在读用 `--primary`（青绿）、已读完用 `--success`（绿）、花费用 `--warning`（琥珀），让数据一眼可辨
2. **书架卡片入场动效**：封面墙加载完毕后 staggered fade-in（50ms 间隔），配合 `prefers-reduced-motion`
3. **详情页进度可视化**：阅读进度 Tab 表格上方添加进度条（当前页 / 总页数），让进度一眼可读
4. **概览图视觉升级**：圆角封面裁切、微妙阴影、品牌色渐变标题区、更好的留白节奏
5. **空状态引导**："书架空空如也"加 CTA 按钮（"了解如何入库"），将空状态转化为引导时刻

---

## 修复计划

| 顺序 | 问题 ID | 严重性 | 修复内容 | 涉及文件 | 状态 |
|------|---------|--------|----------|----------|------|
| 1 | P1-1 | P1 | 占位符 `--cover-light` 降到 36%（全色相验证通过） | `main.css` | ✅ 已修复 |
| 2 | P1-2 | P1 | 添加 `<h1>` 页面标题 | `BookshelfView.vue`, `StatsView.vue`, `OverviewView.vue` | ✅ 已修复 |
| 3 | P1-3 | P1 | Tab 列表添加横向滚动 | `main.css` | ✅ 已修复 |
| 4 | P1-4 | P1 | 交互组件添加 `:active` 态 | `main.css` | ✅ 已修复 |
| 5 | P2-1 | P2 | Canvas 读取 CSS 变量 | `OverviewView.vue` | ✅ 已修复 |
| 6 | P2-2 | P2 | 封面并行加载 | `OverviewView.vue` | ✅ 已修复 |
| 7 | P2-3 | P2 | 条形图动画改 `transform` | `main.css`, `StatsView.vue` | ✅ 已修复 |
| 8 | P2-4 | P2 | Canvas a11y | `OverviewView.vue` | ✅ 已修复 |
| 9 | P2-5 | P2 | 加载状态 ARIA | `OverviewView.vue` | ✅ 已修复 |
| 10 | P2-6 | P2 | 进度表单验证 | `BookDetailView.vue` | ✅ 已修复 |
| 11 | P2-7 | P2 | 统计页空状态 | `StatsView.vue` | ✅ 已修复 |
| 12 | P3-1 | P3 | stat-card 去多余 border | `main.css` | ✅ 已修复 |
| 13 | P3-3 | P3 | 条形图 ARIA | `StatsView.vue` | ✅ 已修复 |
| 14 | P3-4 | P3 | Canvas 字体统一 | `OverviewView.vue` | ✅ 已修复 |

> P3-2（骨架屏动画）优先级极低，暂不列入本轮修复。

---

## 第二轮审计 · 2026-08-09（frontend-design skill）

> 审计视角：设计审美、排版字体、动效微交互、空间构图、视觉细节、残留 Bug
> 审计基准：frontend-design skill guidelines + PRODUCT.md（书房调性 · 安静工具感 · 内容优先）
> 设计方向：「Quiet Library」--温暖纸张感、衬线文学字体、克制的入场动效、有呼吸的空间

### Bug 发现

| ID | 严重性 | 描述 | 位置 |
|----|--------|------|------|
| B1 | P2 | 路由缺少 404 catch-all，未知路径显示空白页 | `router/index.ts` |
| B2 | P3 | `--cover-light` fallback 值 58% 与全局 36% 不一致（BookCover + BookDetailView scoped 样式） | `BookCover.vue:41`, `BookDetailView.vue:441` |
| B3 | P3 | `.skeleton-stat-card` 仍保留 `border: 1px solid var(--border)`，与去边框后的 `.stat-card` 不一致 | `main.css:784` |
| B4 | P3 | OverviewView Canvas aria-label 静态，未包含藏书数量等动态信息 | `OverviewView.vue` |
| B5 | P3 | StatsView 成员统计表格无空数据状态 | `StatsView.vue` |

### 设计提升项

| ID | 类别 | 描述 | 涉及文件 |
|----|------|------|----------|
| D1 | 排版 | 引入 `--font-serif` 衬线字体栈（New York / Georgia / Songti SC），用于书名、页面标题、统计数值、封面占位符首字，赋予文学气质 | `main.css`, `index.html` |
| D2 | 色彩 | 浅色模式背景从冷灰 `#f5f5f5` 暖化为纸张色 `#faf8f5`；hover/active 同步暖化 | `main.css` |
| D3 | 动效 | 书架卡片入场 staggered fade-in-up（30ms 间隔，上限 600ms） | `main.css`, `BookshelfView.vue` |
| D4 | 动效 | 路由切换 fade 过渡（150ms） | `App.vue`, `main.css` |
| D5 | 排版 | 封面占位符首字使用衬线字体 + 内阴影增强质感 | `main.css` |
| D6 | 色彩 | 统计卡片语义着色--在读用 primary、已读完用 success、花费用 warning | `StatsView.vue`, `main.css` |
| D7 | 视觉 | 顶栏底部添加 2px 品牌色强调线 | `main.css` |
| D8 | 视觉 | 书架卡片 hover 封面微缩放（image zoom within overflow:hidden） | `main.css` |
| D9 | 视觉 | 空状态增加装饰性书籍 emoji + CTA 引导 | `BookshelfView.vue` |

### 修复计划

| 顺序 | ID | 类型 | 修复内容 | 状态 |
|------|-----|------|----------|------|
| 1 | B1 | Bug | 添加 404 catch-all 路由 | ✅ 已修复 |
| 2 | B2 | Bug | --cover-light fallback 58% -> 36% | ✅ 已修复 |
| 3 | B3 | Bug | .skeleton-stat-card 去多余 border | ✅ 已修复 |
| 4 | B4 | Bug | Canvas aria-label 动态化 | ✅ 已修复 |
| 5 | B5 | Bug | 成员统计表格空状态 | ✅ 已修复 |
| 6 | D1 | 设计 | 衬线字体系统 | ✅ 已修复 |
| 7 | D2 | 设计 | 暖色纸张背景 | ✅ 已修复 |
| 8 | D3 | 设计 | 卡片入场动效 | ✅ 已修复 |
| 9 | D4 | 设计 | 路由 fade 过渡 | ✅ 已修复 |
| 10 | D5 | 设计 | 占位符衬线字体 | ✅ 已修复 |
| 11 | D6 | 设计 | 统计卡片语义着色 | ✅ 已修复 |
| 12 | D7 | 设计 | 顶栏品牌强调线 | ✅ 已修复 |
| 13 | D8 | 设计 | 卡片 hover 封面缩放 | ✅ 已修复 |
| 14 | D9 | 设计 | 空状态增强 | ✅ 已修复 |
