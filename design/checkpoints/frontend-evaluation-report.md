# 家庭书架 Web UI 前端评估报告

> Checkpoint：2026-08-09 前端修复前评估快照；报告中的问题已经完成对应修复，不代表当前前端质量评分。  
> 评估日期：2026-08-09
> 评估范围：`frontend/` 全部源码（Vue 3 + TypeScript + Vite 5 + Pinia + 纯 CSS）
> 评估依据：`frontend-design` skill（美学维度）+ `impeccable` skill（产品 UI register + critique/audit 框架）
> 评估方法：源码逐文件审查 + 对比度计算 + 反模式清单比对

---

## 一、项目概况

| 维度 | 现状 |
|------|------|
| 技术栈 | Vue 3.5 + TypeScript 5.6 + Vite 5.4 + Pinia 2.2 + Vue Router 4 |
| UI 库 | 无（纯手写 CSS，零组件库依赖） |
| 页面数 | 3 个路由：书架封面墙 `/`、书籍详情 `/books/:id`、统计 `/stats` |
| 组件数 | 1 个可复用组件 `BookCover.vue` + 3 个视图 + 1 个 composable |
| CSS 总量 | 414 行单文件 `main.css`，无预处理器、无 CSS Modules、无 CSS-in-JS |
| 字体 | 系统字体栈：`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif` |
| 色彩系统 | 8 个 CSS 变量（hex 格式），无 OKLCH，无暗色模式 |

### 产品 Register 判定：**Product**

这是一个家庭图书管理工具的 Web 界面，用户在其中完成任务（浏览书架、更新阅读进度、写笔记、查看统计）。设计服务于产品，不是品牌展示。因此适用 `reference/product.md` 的评判标准：**earned familiarity**——工具应消失在任务中，而非通过装饰吸引注意。

---

## 二、设计美学评估（frontend-design 维度）

### 2.1 Typography — ⚠️ 未做任何设计选择

**现状**：全站使用操作系统的默认系统字体栈。没有 `@font-face`，没有 Google Fonts 引入，没有任何字体设计意图。

**问题**：
- 系统字体栈在 macOS 上渲染为 SF Pro，在 Windows 上渲染为 Segoe UI，跨平台一致性差
- 中文排版完全依赖系统默认（苹方/微软雅黑），缺少阅读体验优化
- 标题（`.detail-title` 24px、`.stat-value` 36px）与正文（14px）的字号比尚可，但无字重层次设计——仅靠 `font-weight: 700` vs `400` 区分
- 行高 `1.6` 对正文合理，但标题/数据/表格未分别调整
- 无 `text-wrap: balance`（标题）或 `text-wrap: pretty`（长文本）
- 无 `letter-spacing` 微调

**评判**：product.md 允许系统字体（"System fonts and familiar sans defaults"），但当前实现连基本排版层次都未刻意设计。这是"能用"而非"设计"。

### 2.2 Color & Theme — ⚠️ 功能性够用，无设计意图

**现状**：
```css
--bg: #f5f5f5;        /* 浅灰背景 */
--card-bg: #ffffff;    /* 白色卡片 */
--primary: #2c7a7b;    /* 深青绿 */
--primary-hover: #285e5f;
--text: #1a202c;       /* 近黑 */
--text-muted: #718096;  /* 中灰 */
--border: #e2e8f0;     /* 浅灰边框 */
```

**分析**：
- `#2c7a7b`（深青绿）是一个有辨识度的选择，暗示"书房/知识"调性，不落俗套
- 但整体色彩策略过于 **Restrained**——primary 仅用于按钮、激活态标签和统计数字，其余全是灰白。对于家庭场景，缺少温度感
- 色彩变量用 hex 而非 OKLCH，无法精确控制明度/彩度渐变
- **无暗色模式**——对于晚间阅读场景的家庭书架工具，这是明显缺失
- 错误横幅 `#fed7d7` / `#742a2a` 是标准的红色错误对，功能正确但无设计
- 封面占位符用 `hsl(hash % 360, 45%, 55%)` 生成确定性色彩——这是一个好思路，但 45% 饱和度 + 55% 亮度产生的颜色偏灰暗，在浅灰背景上不够鲜明

**评判**：色彩功能上没有错误，但完全没有"家庭书架"的情感调性。像是通用 admin 模板的默认配色。

### 2.3 Motion — ⚠️ 仅最低限度的过渡

**现状**：
- `.book-card:hover` — `transform: translateY(-2px)` + `box-shadow`，`transition: 0.15s`：功能正确
- `.nav a` — `transition: color 0.2s`：正确
- `.btn:hover` — `transition: background 0.2s`：正确
- 无页面切换动画、无列表渐入、无骨架屏（loading 用纯文本"加载中..."）
- 无 `prefers-reduced-motion` 媒体查询

**评判**：product.md 要求"150–250ms on most transitions"且"Motion conveys state, not decoration"——当前实现符合这一原则，但也暴露了缺少 loading skeleton 的问题。用纯文本"加载中..."替代骨架屏是 product UI 的减分项。

### 2.4 Spatial Composition — ⚠️ 标准网格，无空间节奏

**现状**：
- 书架页：`repeat(auto-fill, minmax(150px, 1fr))` 网格——标准封面墙布局，合理
- 详情页：`280px 1fr` 两栏——左侧封面、右侧信息，标准布局
- 统计页：`repeat(auto-fill, minmax(200px, 1fr))` 卡片网格
- 间距统一使用 `8px / 12px / 16px / 20px / 24px`，有基本节奏感
- 但全站只有一种圆角 `8px`（`--radius`），按钮/输入框用 `4px`——层次不够丰富

**评判**：布局功能正确但完全无个性。每个页面都是标准网格 + 标准卡片，没有空间节奏变化（如详情页可以在摘要区域给予更多留白）。

### 2.5 Backgrounds & Visual Details — ❌ 无

**现状**：
- 背景是纯色 `#f5f5f5`，无纹理、无渐变、无层次
- 卡片只有 `box-shadow: 0 1px 3px rgba(0,0,0,0.1)`——极浅的阴影，几乎不可见
- 无任何装饰性元素

**评判**：对于 product UI，纯色背景可以接受，但当前实现连基本的视觉层次（通过阴影深度区分卡片与背景）都未做到。

---

## 三、技术审计（impeccable audit 框架）

### Audit Health Score

| # | 维度 | 得分 | 关键发现 |
|---|------|------|----------|
| 1 | Accessibility | 1 | `--text-muted` 对比度 3.68:1，低于 AA 要求的 4.5:1，影响所有辅助文本 |
| 2 | Performance | 3 | 懒加载封面、debounce 搜索、路由懒加载——但无骨架屏、滚动监听未节流 |
| 3 | Theming | 1 | 无暗色模式、无 OKLCH、硬编码颜色散落各处、无设计 token 体系 |
| 4 | Responsive Design | 2 | 仅一个 768px 断点、触控目标过小、详情页移动端布局未优化 |
| 5 | Anti-Patterns | 3 | 无 AI slop 特征（无渐变文字、无玻璃态、无侧条纹），但 stat-card 是 SaaS cliché |
| **Total** | | **10/20** | **Acceptable — 需要显著改进** |

---

### 3.1 Accessibility (1/4) — 严重不足

#### P0: `--text-muted: #718096` 对比度不达标

| 场景 | 前景 | 背景 | 对比度 | 要求 | 结果 |
|------|------|------|--------|------|------|
| `.book-author` (12px) | #718096 | #ffffff | 4.02:1 | 4.5:1 (AA 正常文本) | **FAIL** |
| `.detail-meta` (14px) | #718096 | #f5f5f5 | 3.68:1 | 4.5:1 | **FAIL** |
| `.stat-label` (13px) | #718096 | #ffffff | 4.02:1 | 4.5:1 | **FAIL** |
| `.member-selector label` (13px) | #718096 | #ffffff | 4.02:1 | 4.5:1 | **FAIL** |
| `.nav a` (14px, 非激活) | #718096 | #ffffff | 4.02:1 | 4.5:1 | **FAIL** |

**影响**：全站辅助文本（作者名、元数据、标签、导航项）在大多数设备上难以清晰阅读。家庭场景中可能有老年成员，此问题尤为突出。

**修复建议**：将 `--text-muted` 从 `#718096` 调暗至 `#5a6878` 或更暗（需 ≥ 4.5:1）。

#### P0: 禁用按钮对比度严重不足

`.btn:disabled` 白色文字 on `#cbd5e0` 背景：对比度仅 **1.49:1**，远低于 4.5:1 最低要求。禁用状态几乎不可读。

#### P1: 缺少 ARIA 标签和语义化

- 顶栏 `<nav>` 无 `aria-label="主导航"`
- 成员选择器 `<select>` 的 `<label>` 未通过 `for`/`id` 关联
- Tab 切换 (`button.tab`) 无 `role="tab"` / `aria-selected` / `aria-controls`
- 错误横幅无 `role="alert"` / `aria-live="assertive"`
- `<a href="javascript:void(0)">` 返回链接——应使用 `<button>` 语义

#### P1: 键盘导航缺失

- Tab 切换面板无键盘箭头导航（`aria-activedescendant` 模式）
- 书架无限滚动无"加载更多"的键盘可操作按钮（纯 scroll 监听）
- 无 `:focus-visible` 样式（仅靠浏览器默认 outline）

#### P1: 表单可访问性

- `<label>` 与 `<input>` 未通过 `for`/`id` 关联（`BookDetailView.vue` 所有表单）
- `<select>` 缺少 `aria-label`
- 必填字段无 `aria-required`
- 错误提示无 `aria-describedby` 关联

#### P2: 图片 alt 文本

- 封面 `<img>` 有 `:alt="book.title"` ✓
- 但 `BookCover.vue` 的占位符 `<div>` 无 `role="img"` / `aria-label`

---

### 3.2 Performance (3/4) — 良好，有改进空间

#### 正面发现 ✓

- **路由懒加载**：所有视图使用 `() => import()` 动态导入 ✓
- **封面懒加载**：`BookCover.vue` 使用 `loading="lazy"` ✓
- **搜索 debounce**：300ms debounce 防止频繁请求 ✓
- **类型安全**：TypeScript strict 模式开启 ✓
- **轻量依赖**：仅 vue / vue-router / pinia，无 UI 库开销 ✓

#### P1: 滚动监听未节流

```typescript
// BookshelfView.vue:34-39
function handleScroll() {
  const scrolled = window.scrollY + window.innerHeight
  const total2 = document.documentElement.scrollHeight
  if (scrolled >= total2 - 200) {
    loadMore()
  }
}
window.addEventListener('scroll', handleScroll)  // 无 throttle/passive
```

每次滚动事件触发都会读取 `scrollY` + `scrollHeight`（强制回流），在高频滚动时可能造成卡顿。应使用 `IntersectionObserver` 或至少 `requestAnimationFrame` 节流。

#### P2: 无骨架屏

Loading 状态用纯文本"加载中..."，product.md 明确要求"Skeleton states for loading, not spinners in the middle of content"。书架页应显示骨架封面卡片，详情页应显示骨架布局。

#### P2: 成员列表重复加载

```typescript
// main.ts:14 — 启动时加载
members.load().catch(() => {})

// App.vue:9-11 — onMounted 再次加载
if (members.members.length === 0) {
  members.load().catch(() => {})
}
```

虽然 `members.length === 0` 检查可以避免重复请求，但两次调用之间的竞态可能导致冗余请求。

#### P3: 无 `<link rel="preload">` 封面图片

对于首屏可见的封面图片，可使用 `rel="preload"` 优化 LCP。

---

### 3.3 Theming (1/4) — 基础薄弱

#### P1: 无暗色模式

家庭书架工具的典型使用场景包括晚间阅读，暗色模式是基本需求。当前 CSS 完全没有 `prefers-color-scheme` 媒体查询。

#### P1: 硬编码颜色散落

尽管定义了 CSS 变量，多处仍使用硬编码颜色：

| 文件 | 行号 | 硬编码值 | 应使用 |
|------|------|----------|--------|
| `main.css:84` | `background: white` | `var(--card-bg)` |
| `main.css:90` | `background: #fed7d7` | `var(--error-bg)` |
| `main.css:91` | `color: #742a2a` | `var(--error-text)` |
| `main.css:129` | `background: #e2e8f0` | `var(--border)` |
| `main.css:182` | `background: white` | `var(--card-bg)` |
| `main.css:237` | `background: #e6fffa` | `var(--tag-bg)` |
| `main.css:328` | `background: #cbd5e0` | `var(--disabled-bg)` |
| `BookDetailView.vue` | 多处 `style="color: var(--text-muted)"` | 内联样式应提取为类 |

#### P2: 无 OKLCH 色彩空间

所有颜色使用 hex 格式。impeccable skill 建议使用 OKLCH 以获得更均匀的感知明度梯度。

#### P2: 缺少语义化色彩 token

当前只有 8 个基础变量，缺少完整的状态色彩体系：
- 无 `--success` / `--warning` / `--info` 语义色
- 无 `--hover-bg` / `--active-bg` 交互态
- 无 `--skeleton-bg` 骨架屏色
- 无 `--focus-ring` 焦点环色

#### P3: 无主题切换机制

即使添加暗色模式，也没有主题切换 UI 和持久化逻辑。

---

### 3.4 Responsive Design (2/4) — 有基础但粗糙

#### P1: 仅一个断点

```css
@media (max-width: 768px) {
  .detail-layout { grid-template-columns: 1fr; }
  .book-grid { grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); }
}
```

- 无 `≤480px` 手机断点（封面墙 110px 在小屏上仍可能太小或太大）
- 无 `≥1200px` 大屏断点（`max-width: 1400px` 的内容区在大屏上浪费空间）
- 统计页 `.stats-grid` 无响应式调整

#### P1: 触控目标过小

| 元素 | 尺寸 | 要求 | 结果 |
|------|------|------|------|
| `.nav a` padding | 4px 8px ≈ 22px 高 | ≥ 44px | **FAIL** |
| `.member-selector select` | 4px 8px ≈ 22px 高 | ≥ 44px | **FAIL** |
| `.filter-bar select` | 6px 12px ≈ 28px 高 | ≥ 44px | **FAIL** |
| `.tab` | 8px 16px ≈ 32px 高 | ≥ 44px | **FAIL** |
| `.btn` | 6px 16px ≈ 28px 高 | ≥ 44px | **FAIL** |

在移动设备上，所有交互元素都远低于 44×44px 的最低触控目标。

#### P2: 顶栏在移动端可能溢出

顶栏使用 `flex` + `gap: 24px` + logo + 导航 + 成员选择器水平排列，在窄屏上会挤压或溢出。无汉堡菜单或折叠逻辑。

#### P2: 详情页表格在移动端横向溢出

`.data-table` 使用 `width: 100%`，但列内容（如 ISBN、订单号）可能超出宽度。无 `overflow-x: auto` 包装。

---

### 3.5 Anti-Patterns (3/4) — 基本干净

#### 正面发现 ✓

- ✅ 无渐变文字（`background-clip: text`）
- ✅ 无玻璃态（`backdrop-filter` 装饰性使用）
- ✅ 无侧条纹边框
- ✅ 无 AI 调色板（紫色渐变 on 白色）
- ✅ 无编号 section 标记（01/02/03）
- ✅ 无 uppercase tracked eyebrow
- ✅ 无弹性/弹跳动画
- ✅ 封面占位符的确定性色彩是一个有意图的设计

#### P2: stat-card 是 SaaS cliché

统计页的 5 个 `.stat-card`（大数字 + 小标签）是 impeccable skill 明确指出的"hero-metric template"——"Big number, small label, supporting stats, gradient accent. SaaS cliché."虽然此处无渐变，但模式本身是模板化的。

#### P3: 卡片网格密度均匀

书架的 `.book-card` 全部相同尺寸、相同布局。product.md 允许一致性，但"Identical card grids"在 anti-patterns 清单中。对于封面墙这是合理的（封面尺寸本应一致），但可考虑在首屏或推荐位增加特色卡片。

---

## 四、UX 体验评估

### 4.1 空状态

```html
<!-- BookshelfView.vue:73 -->
<div class="empty">书架空空如也，用 CLI 或 Agent 入库吧</div>
```

**评价**：空状态文案有引导性（指向 CLI/Agent），但缺少操作按钮或文档链接。product.md 要求"Empty states that teach the interface, not 'nothing here.'"——当前实现介于两者之间。

### 4.2 错误处理

```html
<!-- App.vue:36-38 -->
<div v-if="lastError" class="error-banner" @click="lastError = null">
  ⚠ {{ lastError }}
</div>
```

**评价**：
- 点击关闭——简单有效 ✓
- 但无 `role="alert"`，屏幕阅读器不会播报
- 后端未启动时的静默处理（`main.ts:15` 注释"后端未启动时静默处理"）——用户看到的是空页面而非连接错误提示，体验不佳
- `alert()` 用于"请先在顶栏选择成员"（`BookDetailView.vue:62, 82`）——应使用 inline 提示或 toast

### 4.3 成员选择器

**评价**：
- 成员选择结果持久化到 `localStorage` ✓
- 但 `<select>` 下拉在成员多时不易浏览（无搜索、无头像）
- 切换成员后不会自动刷新当前页面数据（如详情页的进度表）

### 4.4 详情页 Tab

**评价**：
- 6 个 Tab（进度/副本/购买/笔记/附件/自定义）——功能完整 ✓
- 但 Tab 切换无 URL hash 持久化，刷新后回到默认 Tab
- Tab 内容区无过渡动画，切换生硬
- "更新进度"表单始终显示，即使无成员选中——应禁用或提示

### 4.5 无限滚动

**评价**：
- 滚动加载实现基本正确 ✓
- 但无"回到顶部"按钮
- 无总页数/当前加载进度指示
- 筛选条件变化时 `loadInitial()` 会重置列表，但如果用户在滚动较远位置，页面不会回到顶部

---

## 五、架构与代码质量

### 5.1 正面发现

- **TypeScript strict 模式**：`tsconfig.json` 开启 `strict`, `noUnusedLocals`, `noUnusedParameters` ✓
- **Composable 模式**：`useBooks` 封装了书架列表逻辑，可复用 ✓
- **Store 职责清晰**：`api.ts` 只管请求，`members.ts` 只管成员状态 ✓
- **API 响应统一处理**：`request<T>` 函数统一处理 `ok`/`error` 字段 ✓
- **路由懒加载**：按需加载视图组件 ✓

### 5.2 改进空间

#### P2: API store 未处理网络错误

```typescript
// api.ts:11-13
const res = await fetch(`${BASE}${path}`, { ... })
const body: ApiResponse<T> = await res.json().catch(() => ({
  ok: false, data: null as any, error: `HTTP ${res.status}`,
}))
```

`fetch` 本身的网络错误（离线、DNS 失败）会抛出异常，未被捕获。应包裹 `try-catch` 或在调用方处理。

#### P2: useBooks 的 watch 触发问题

```typescript
// useBooks.ts:65-69
watch(
  () => ({ ...filters }),
  () => loadInitial(),
  { deep: true },
)
```

`filters` 是 `reactive` 对象，每次 spread 都会创建新对象引用，`deep: true` 是多余的。且 keyword 的 debounce 在外部处理（`BookshelfView.vue`），但 `watch` 会在 debounce 后的 `filters.keyword` 变化时触发——逻辑正确但有隐含耦合。

#### P3: 内联样式过多

`BookDetailView.vue` 和 `StatsView.vue` 大量使用 `style="..."` 内联样式：

```html
<!-- BookDetailView.vue:68 -->
<span style="color: var(--text-muted); font-size: 13px;">共 {{ total }} 本</span>

<!-- StatsView.vue:30 -->
<h2 style="margin-bottom: 20px;">藏书统计</h2>
```

应提取为 CSS 类，便于维护和主题切换。

---

## 六、改进优先级排序

### P0 — 阻塞级（发布前必须修复）

| # | 问题 | 影响面 | 建议命令 |
|---|------|--------|----------|
| 1 | `--text-muted` 对比度 3.68:1 < 4.5:1 | 全站辅助文本不可读 | `/impeccable polish` |
| 2 | 禁用按钮对比度 1.49:1 | 禁用态完全不可读 | `/impeccable polish` |
| 3 | 表单 `<label>` 未关联 `<input>` | 屏幕阅读器无法操作表单 | `/impeccable harden` |

### P1 — 重要级（下个版本应修复）

| # | 问题 | 影响面 | 建议命令 |
|---|------|--------|----------|
| 4 | 触控目标 < 44px | 移动端操作困难 | `/impeccable adapt` |
| 5 | 无暗色模式 | 晚间阅读刺眼 | `/impeccable colorize` |
| 6 | 滚动监听未节流 | 滚动性能差 | `/impeccable optimize` |
| 7 | Tab 无 ARIA 语义 | 键盘/辅助技术不可用 | `/impeccable harden` |
| 8 | 仅一个响应式断点 | 平板/手机体验粗糙 | `/impeccable adapt` |
| 9 | 硬编码颜色散落 | 主题切换困难 | `/impeccable document` → token 化 |
| 10 | 无骨架屏 | 加载体验差 | `/impeccable delight` |

### P2 — 改进级（有空时修复）

| # | 问题 | 影响面 | 建议命令 |
|---|------|--------|----------|
| 11 | 内联样式过多 | 可维护性差 | `/impeccable layout` |
| 12 | stat-card 是 SaaS cliché | 设计缺乏个性 | `/impeccable bolder` |
| 13 | 顶栏移动端无折叠 | 窄屏溢出 | `/impeccable adapt` |
| 14 | `alert()` 用于提示 | 打断流式体验 | `/impeccable clarify` |
| 15 | Tab 切换无 URL 持久化 | 刷新丢失状态 | `/impeccable harden` |
| 16 | 封面占位符饱和度偏低 | 视觉不够鲜明 | `/impeccable colorize` |
| 17 | 后端离线时无连接提示 | 用户困惑 | `/impeccable onboard` |

### P3 — 打磨级

| # | 问题 | 建议命令 |
|---|------|----------|
| 18 | 无 `prefers-reduced-motion` | `/impeccable animate` |
| 19 | 无 `:focus-visible` 样式 | `/impeccable polish` |
| 20 | 成员切换不自动刷新数据 | `/impeccable harden` |
| 21 | 无"回到顶部"按钮 | `/impeccable delight` |
| 22 | 封面图片无 `rel="preload"` | `/impeccable optimize` |
| 23 | hex 色彩未升级 OKLCH | `/impeccable colorize` |

---

## 七、总体评价

### 得分卡

| 维度 | 得分 | 评价 |
|------|------|------|
| 设计美学 | 4/10 | 功能性 UI，无设计意图。系统字体、单色系、无装饰——"能用"但无个性 |
| 可访问性 | 2/10 | 对比度不达标、无 ARIA、表单未关联——基本不可访问 |
| 性能 | 7/10 | 懒加载和 debounce 做了，但滚动节流和骨架屏缺失 |
| 主题/色彩 | 2/10 | 无暗色模式、硬编码散落、无 OKLCH、token 体系不完整 |
| 响应式 | 3/10 | 仅一个断点、触控目标过小、移动端布局粗糙 |
| 反模式 | 7/10 | 基本无 AI slop，但 stat-card 是模板化设计 |
| 代码质量 | 6/10 | TypeScript strict、composable 模式——但内联样式多、错误处理不完整 |
| UX 体验 | 5/10 | 功能完整但粗糙——alert 提示、无骨架屏、空状态弱 |
| **综合** | **4.5/10** | **可用的 MVP，但距离 production-grade 有显著差距** |

### 一句话总结

> 这是一个功能完整的 MVP 前端，技术栈选择合理（Vue 3 + TS + Vite），但在可访问性、响应式设计和视觉设计三个维度上存在系统性缺陷。最紧迫的问题是对比度不达标（影响所有辅助文本）和触控目标过小（影响所有移动端用户），这些应在下一次发布前修复。

### 建议的改进路径

```
1. /impeccable polish     — 修复对比度、禁用态、焦点样式（P0）
2. /impeccable harden     — 修复 ARIA、表单关联、错误处理（P0-P1）
3. /impeccable adapt      — 修复触控目标、响应式断点、顶栏折叠（P1）
4. /impeccable colorize   — 添加暗色模式、OKLCH 色彩、占位符饱和度（P1）
5. /impeccable optimize   — 滚动节流、骨架屏、preload（P1-P2）
6. /impeccable document   — 提取设计 token、生成 DESIGN.md（P1-P2）
7. /impeccable bolder     — 打破 SaaS cliché、增加设计个性（P2）
8. /impeccable delight    — 骨架屏、回到顶部、加载动效（P2-P3）
9. /impeccable polish     — 最终打磨（收尾）
```

---

## 八、修复状态（2026-08-09 更新）

> 本报告为**评估时点快照**（综合评分 4.5/10），记录的是修复前的现状与建议。
> 上述 P0–P3 共 23 项 + 后续两轮审计的 14 + 14 项**已全部修复**，
> 详细修复追踪见同目录 [`frontend-audit-2026-08-09.md`](./frontend-audit-2026-08-09.md)（含修复计划表与逐项 ✅ 标注）。

### 已落地的关键改进

| 维度 | 原评分 | 改进内容 |
|------|--------|----------|
| 可访问性 | 2/10 | 对比度全部 ≥ 4.5:1（`--text-muted` 5.7:1 / 禁用态 5.06:1）；ARIA tablist + 键盘导航；表单 `label`/`id` 全关联；`role="img"` 占位符；`role="alert"` 错误横幅；页面 `<h1>` 标题层级 |
| 主题/色彩 | 2/10 | 完整 `prefers-color-scheme: dark` token 体系；语义色（success/warning/error）；暖纸色背景；衬线字体系统 |
| 响应式 | 3/10 | 三断点（480/768/1200px）；触控目标 44px；顶栏移动端防溢出；Tab 横向滚动 |
| 性能 | 7/10 | 滚动 `requestAnimationFrame` 节流；shimmer 骨架屏（三页）；概览图封面并行加载 |
| 反模式 | 7/10 | stat-card 去双重轮廓；交互组件 `:active` 按压态；语义着色 |
| 安全 | — | SPA fallback 路径穿越护栏（`is_relative_to`，BUG-106） |

### 相关 task-list 记录

- OPT-005：前端评估报告 23 项系统性修复
- BUG-106：SPA fallback 路径穿越 P0（已修复）
- BUG-107：OverviewView 挂载竞态（已修复）
- BUG-108/110：前端自查 4 项 + 测试环境耦合（已修复）
