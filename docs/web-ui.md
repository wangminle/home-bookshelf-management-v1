# Web UI 部署指南

家庭书架 Web UI 是一个 Vue 3 SPA，提供封面墙浏览、筛选、书籍详情和阅读统计功能。

## 开发模式

```bash
# 终端 1：启动后端
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir .

# 终端 2：启动前端开发服务器（自动代理 /api → :8000）
cd frontend
npm install
npm run dev
```

开发服务器运行在 `http://localhost:3000`，API 请求自动代理到后端 `:8000`。

## 生产构建

```bash
cd frontend
npm run build
```

构建产物在 `frontend/dist/`，包含 `index.html` + 静态资源（JS/CSS）。

### 部署方式一：nginx 反向代理（推荐）

nginx 同时托管前端静态文件和后端 API：

```nginx
server {
    listen 80;
    server_name bookshelf.lan;

    # 前端静态文件
    location / {
        root /path/to/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 部署方式二：后端直接托管（轻量，推荐）

将构建产物拷入 `backend/static/`，后端 `main.py` 通过 SPA fallback 同时服务 API 和前端：

```bash
cd frontend
npm install && npm run build
cp -r dist/* ../backend/static/
```

后端代码已内置此逻辑（无需手动修改 `main.py`）：

```python
# backend/app/main.py（已实现）
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(...), name="assets")
    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        # 静态文件直接返回，其余路径回退到 index.html
        ...
```

`/api/v1` 路由优先匹配，`/assets` 挂载静态资源，其余路径返回 `index.html` 让 vue-router 接管。单端口同时服务 API + SPA。

> `backend/static/` 已在 `.gitignore` 中忽略，是构建产物不入库。

### 部署方式三：lwa 本地部署

详见 [部署](./deployment.md#lwa-本地部署家庭服务器推荐)。lwa 自动生成 Dockerfile 并管理容器，前端构建产物同样需拷入 `backend/static/`。

### 路径别名部署（Path Alias）

当应用部署在非根路径（如反向代理的路径别名 `/home-bookshelf/`）时，前端需要在构建时指定 base path，使静态资源路径、路由基址和 API 请求路径三处对齐。

**构建命令：**

```bash
cd frontend
VITE_BASE=/home-bookshelf/ npm run build
cp -r dist/* ../backend/static/
```

`VITE_BASE` 会被 Vite 注入为 `import.meta.env.BASE_URL`，前端三处自动对齐：

| 组件 | 默认（`/`） | 别名（`/home-bookshelf/`） |
|------|------------|--------------------------|
| 静态资源（JS/CSS） | `/assets/...` | `/home-bookshelf/assets/...` |
| Vue Router history | `/` | `/home-bookshelf/` |
| API 请求基址 | `/api/v1` | `/home-bookshelf/api/v1` |

**后端无需修改。** 反向代理（如 Caddy `handle_path`）会剥离别名前缀后转发给后端，后端仍收到 `/api/v1/...`，路由保持绝对根路径。

**反向代理示例（Caddy）：**

```
/home-bookshelf/* {
    handle_path /home-bookshelf/* {
        reverse_proxy 127.0.0.1:8000
    }
}
```

`handle_path` 会自动剥离 `/home-bookshelf` 前缀，后端收到的是 `/`、`/api/v1/...`、`/assets/...` 等标准路径。

> **注意**：直连部署（hostPort 或后端直接托管）时不要设置 `VITE_BASE`（默认 `/`），否则资源路径会多出前缀导致 404。

## 功能范围

| 功能 | 状态 |
|------|------|
| 封面墙网格浏览（无限滚动） | ✅ |
| 按关键词/状态/分类筛选 | ✅ |
| 书籍详情页（元数据/副本/进度/购买/笔记/附件/自定义字段） | ✅ |
| 更新阅读进度 | ✅ |
| 添加读书笔记 | ✅ |
| 藏书统计仪表盘 | ✅ |
| 成员选择器（写操作归属） | ✅ |
| 封面/附件图片展示 | ✅ |
| 年度趋势统计（入库/花费/阅读页数按年汇总） | ✅ |
| 花费趋势条形图 | ✅ |
| 书架概览图生成（封面拼图 + 统计摘要 + 分类 TOP3） | ✅ |
| 概览图导出 PNG / 分享 | ✅ |

### 概览图功能

访问 `/overview` 页面可一键生成"我家书架概览图"：

- **布局**：1080×1350 竖版（Instagram 比例），顶部标题、中部 6×4 封面墙拼图、底部统计摘要（藏书/在读/已读完/花费）+ 分类 TOP3 条形图
- **无封面处理**：无封面的书用书名首字 + 确定性色相色块填充
- **导出**：`canvas.toBlob()` → PNG 下载
- **分享**：支持 `navigator.share()`（移动端），桌面端降级为下载
- **技术**：纯原生 Canvas API，不依赖第三方图表/画布库

## 鉴权模型

Web UI 使用 Owner 密码登录（前端「Agent 授权」页首次设置）：登录后持有 `hbs_session` 会话 Cookie，全部业务请求凭该会话通过统一鉴权（AuthContext）；`X-UI-Client` 头已无任何授权含义。Owner 会话可在顶栏切换家庭成员并代表其操作（写请求携带所选 `member_id`）；外部 Agent/CLI 走 Bearer Token 或渠道头，只能操作绑定成员本人的数据。**请勿将 Web UI 直接暴露到公网。**

## 技术栈

- Vue 3 + TypeScript + Vite 5
- Vue Router 4（懒加载路由）
- Pinia（状态管理）
- 纯 CSS（无 UI 组件库，对标 calibre-web / komga 的网格风格）

### 设计系统

前端使用 CSS 变量（design token）体系，所有颜色/间距/圆角均通过 `var(--...)` 引用，详见 [`design/plans/frontend-audit-2026-08-09.md`](../design/plans/frontend-audit-2026-08-09.md)。

- **暗色模式**：完整 `@media (prefers-color-scheme: dark)` token 覆盖，跟随系统主题，晚间阅读不刺眼
- **可访问性（A11y）**：对比度全部 ≥ WCAG AA（4.5:1）；ARIA tablist + 键盘导航；表单 `label`/`id` 关联；`role="alert"` 错误播报；`role="img"` 占位符；页面 `<h1>` 标题层级
- **响应式**：三断点（≤480px 手机 / ≤768px 平板 / ≥1200px 大屏）；触控目标 ≥ 44px；顶栏移动端防溢出
- **骨架屏**：书架页 / 详情页 / 统计页均使用 shimmer 骨架屏替代 spinner
- **性能**：封面 `loading="lazy"`；滚动 `requestAnimationFrame` 节流；路由懒加载；概览图封面并行加载

### 安全

后端 SPA fallback（`main.py:spa_fallback`）已实现路径穿越护栏（`is_relative_to`），阻止 `/../` 形式读取 `backend/` 旁路文件。详见 [BUG-106](../task-list.md)。
