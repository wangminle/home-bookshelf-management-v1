# Web UI 部署指南

家庭书架 Web UI 是一个 Vue 3 SPA，提供封面墙浏览、筛选、书籍详情、阅读统计、概览图导出，以及批量入库工作台（`/batch-intake`）、模型设置（`/llm-settings`）、访问策略（`/catalog-policy`）、Agent 授权管理（`/agent-authorization`）、实体书架位置管理（`/storage`）等管理页面。

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

> 注意：vite dev 代理 `/api`、`/auth`、`/agent-access`（`vite.config.ts`；后两者为根级路由，2026-08-22 补充代理），Owner 登录页与 Agent 授权页在开发态可用；其余根级路由（`/agent`、`/skills`、`/mcp`）仍不代理，相关页面需走生产构建（后端托管或完整反代）验证。

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

    # 根级后端路由：只代理 /api/ 会导致登录页与 Agent 授权页失效。
    # /auth（Owner 登录/登出/初始化密码）、/agent-access（Agent 授权管理）、
    # /agent（Agent 发现面 manifest/bootstrap/openapi/skills）、
    # /.well-known（api-catalog）、/llms.txt 都挂在后端根路径下。
    location ~ ^/(auth|agent-access|agent|\.well-known)/|^/llms\.txt$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

> 简化做法：也可以除 `/assets` 等静态资源外全部 `proxy_pass` 到后端，由后端 SPA fallback 托管前端（见部署方式二）。

### 部署方式二：后端直接托管（轻量，推荐）

仓库根目录执行一键脚本，构建产物进入 `backend/static/`（`rsync --delete`，排除 `skills/` bundle）：

```bash
bash scripts/deploy_frontend.sh
```

路径别名：

```bash
bash scripts/deploy_frontend.sh --base /home-bookshelf/
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

> `backend/static/` 已在 `.gitignore` 中忽略：构建产物不入库，部署前需运行 `scripts/deploy_frontend.sh`（lwa 不会自动构建前端）。

### 部署方式三：lwa 本地部署

详见 [部署](./deployment.md#lwa-本地部署家庭服务器推荐)。升级前先跑 `bash scripts/deploy_frontend.sh --base /<alias>/`，再 `lwa rebuild`。

### 路径别名部署（Path Alias）

当应用部署在非根路径（如反向代理的路径别名 `/home-bookshelf/`）时，前端需要在构建时指定 base path，使静态资源路径、路由基址和 API 请求路径三处对齐。

**构建命令：**

```bash
bash scripts/deploy_frontend.sh --base /home-bookshelf/
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
| 匿名共享书架 `/shared`（C 模式：脱敏书目浏览/搜索/详情，权限阶段 1） | ✅ |
| 统一登录页 `/login`（Owner/Member 共用，权限阶段 2） | ✅ |
| 角色化导航（Member 无管理入口/无成员切换器；顶栏退出） | ✅ |
| `/agent` Agent 连接信息页 | ✅ |
| `/agent-authorization` Agent 授权管理（密码初始化 / 签发 Token；登录走 `/login`） | ✅ |
| `/agent-access` 授权列表页 | ✅ |
| `/llm-settings` 模型设置（多模态识图模型：enabled 开关、显示名称、Base URL、模型 ID、API Key 只回显末四位可清除、超时/最大 token/温度/识图精细度 auto-low-high，存本机数据库供批量入库封面识别） | ✅ |
| `/batch-intake` 拍照批量入库核对工作台（Owner 专属，`v-if="sessionRole === 'owner'"`，后端全部端点 require_owner；新建批次 → 上传多图 → 模型识别（需先在「模型」页启用）→ 候选核对（编辑书名/副标题/作者/ISBN、拆分照片）→ 匹配已有书预览 → 勾选确认 → 执行入库 → 回执/失败重试；状态服务端持久化，刷新可恢复；重复执行不重复建书）。0.4.0 新增：① ISBN 归属冲突候选展示冲突书目，需显式勾选「强制关联此书」确认时才会携带 `resolutions` `force_link`，且修改字段后须重新核对匹配目标（后端会清空匹配结果）；② 中断任务的「恢复执行」入口（任务 executing 或 failed 且仍有 executing 命令时可用，安全续跑不重复建书）；③ 逐照片识别警告展示（识别失败/字段异常的 `code` + `message`，照片卡片下方黄条列出） | ✅ |
| `/catalog-policy` 访问策略页（逐书可见级别 + C→B 预览） | ✅ |
| `/storage` 实体书架管理页（Owner 建档/编辑/归档房间与书架，Member 只读；书架布局编辑器支持可变层格、带引用结构调整 409 引导；版本冲突自动刷新，刷新失败锁定表单直到手动重载成功） | ✅ |
| `/storage/shelves/:id` 书架详情页（层格示意图、格子副本清单分页、书架照片上传/设主图/删除、`?cell=` 深链高亮；路由切换自动重载，加载失败不保留旧架可写界面） | ✅ |
| 书籍详情页位置卡片（副本结构化位置展示与「查看位置」跳转；Owner 编辑/清除单册位置、补录实体副本；补录结果未确认时同载荷复用幂等键重试，不重复建册） | ✅ |

### 概览图功能

访问 `/overview` 页面可一键生成"我家书架概览图"：

- **布局**：1080×1350 竖版（Instagram 比例），顶部标题、中部 6×4 封面墙拼图、底部统计摘要（藏书/在读/已读完/花费）+ 分类 TOP3 条形图
- **无封面处理**：无封面的书用书名首字 + 确定性色相色块填充
- **导出**：`canvas.toBlob()` → PNG 下载
- **分享**：支持 `navigator.share()`（移动端），桌面端降级为下载
- **技术**：纯原生 Canvas API，不依赖第三方图表/画布库

## 鉴权模型

Web UI 通过统一登录页 `/login` 登录（权限阶段 2：Owner 与家庭成员使用各自用户名+密码；Owner 密码在「Agent 授权」页首次设置，成员账号由 Owner 创建并设置密码）：登录后持有 `hbs_session` 会话 Cookie，全部业务请求凭该会话通过统一鉴权（AuthContext），`X-UI-Client` 头无任何授权含义。角色/密码/停用变更后受影响 Web 会话立即失效；角色变更与密码重置还会同时吊销该成员的全部 Agent Token（停用成员的 Agent Token 亦会在校验时被拒）。Owner 会话可在顶栏切换家庭成员并代表其操作（写请求携带所选 `member_id`，操作日志记录实际操作者与数据归属人）；Member 登录后固定本人身份、无成员切换器、不显示管理入口；外部 Agent/CLI 走 Bearer Token 或渠道头，只能操作绑定成员本人的数据。**请勿将 Web UI 直接暴露到公网。**

## 技术栈

- Vue 3 + TypeScript + Vite 5
- Vue Router 4（懒加载路由）
- Pinia（状态管理）
- 纯 CSS（无 UI 组件库，对标 calibre-web / komga 的网格风格）

### 设计系统

前端使用 CSS 变量（design token）体系，所有颜色/间距/圆角均通过 `var(--...)` 引用；2026-08-09 的修复复盘见 [`design/checkpoints/frontend-audit-2026-08-09.md`](../design/checkpoints/frontend-audit-2026-08-09.md)。

- **暗色模式**：完整 `@media (prefers-color-scheme: dark)` token 覆盖，跟随系统主题，晚间阅读不刺眼
- **可访问性（A11y）**：对比度全部 ≥ WCAG AA（4.5:1）；ARIA tablist + 键盘导航；表单 `label`/`id` 关联；`role="alert"` 错误播报；`role="img"` 占位符；页面 `<h1>` 标题层级
- **响应式**：三断点（≤480px 手机 / ≤768px 平板 / ≥1200px 大屏）；触控目标 ≥ 44px；顶栏移动端防溢出
- **骨架屏**：书架页 / 详情页 / 统计页均使用 shimmer 骨架屏替代 spinner
- **性能**：封面 `loading="lazy"`；滚动 `requestAnimationFrame` 节流；路由懒加载；概览图封面并行加载

### 安全

后端 SPA fallback（`main.py:spa_fallback`）已实现路径穿越护栏（`is_relative_to`），阻止 `/../` 形式读取 `backend/` 旁路文件。详见 [BUG-106](../task-list.md)。
