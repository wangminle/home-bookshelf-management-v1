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

### 部署方式二：后端直接托管（轻量）

将构建产物放入后端，由 FastAPI 的 StaticFiles 托管：

```python
# 在 app/main.py 末尾添加（需 from fastapi.staticfiles import StaticFiles）
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
```

> 注意：此方式需确保 `api_router` 的 `/api/v1` 路由优先匹配，StaticFiles 挂载在最后。

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

## 鉴权模型

Web UI 继承一期的局域网信任模型：不做浏览器登录，写操作时从顶栏选择家庭成员，请求体中携带 `member_id`，后端走 `resolve_member_id` 兜底。**请勿将 Web UI 直接暴露到公网。**

## 技术栈

- Vue 3 + TypeScript + Vite 5
- Vue Router 4（懒加载路由）
- Pinia（状态管理）
- 纯 CSS（无 UI 组件库，对标 calibre-web / komga 的网格风格）
