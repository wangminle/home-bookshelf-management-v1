# 部署

把 API 常驻在家庭服务器上，并做好数据备份。

---

## Docker（推荐）

```bash
cd deploy
cp .env.example .env    # 按需修改 BOOKSHELF_BIND / BOOKSHELF_DATA_DIR
docker compose up -d
docker compose ps
curl http://127.0.0.1:8000/api/v1/public-health   # /health 需认证（members:read），无凭证探活用 public-health
```

默认把宿主机数据目录挂到容器 `/data`（库文件、封面、附件）。

常用：

```bash
docker compose logs -f bookshelf-api
docker compose down
```

如需同时托管 Web UI，先构建前端再启动后端：

```bash
cd frontend && npm install && npm run build && cd ..
cp -r frontend/dist/* backend/static/
cd deploy && docker compose up -d
```

后端 `main.py` 检测到 `backend/static/` 目录后自动启用 SPA fallback，同时服务 API 和前端。

---

## lwa 本地部署（家庭服务器推荐）

[lwa（Local Webpage Access）](https://github.com/nicekate/local-webpage-access) 是轻量本地网页部署工具，自动生成 Dockerfile/compose 并管理端口。适合家庭服务器一键部署。

```bash
# 从 lwa 工作区导入后端目录
cd <lwa-workspace>
lwa import --from-dir <项目路径>/backend --name "家庭图书管理" --yes

# 修改启动命令加入 alembic 迁移（lwa 生成的 Dockerfile 默认不跑迁移）
# 编辑 apps/<instance-id>/local-web.json，将 start 改为：
#   sh -c "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir ."

# 启动
lwa start <instance-id>
```

部署前需将前端构建产物拷入 `backend/static/`，后端会自动托管。

如果使用 lwa 路径别名（path alias）部署，构建前端时需指定 `VITE_BASE`：

```bash
cd frontend
VITE_BASE=/<alias>/ npm run build
cp -r dist/* ../backend/static/
```

详见 [Web UI 部署 · 路径别名](./web-ui.md#路径别名部署path-alias)。

管理页：`http://<服务器IP>:17800`

---

## 本机 / venv

```bash
cd backend
bash install.sh         # 或 install.bat
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir .
```

生产可用 systemd 单元：`deploy/systemd/bookshelf.service`（按实际路径改 `WorkingDirectory` / `ExecStart`）。

---

## 环境变量（节选）

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | 默认 `sqlite:///./data/bookshelf.db` |
| `DATA_DIR` | 数据根目录（封面、附件） |
| `GOOGLE_BOOKS_API_KEY` | 可选 |
| `SETUP_TOKEN` | 可选；保护白名单建立后的 `/members/bind`。CLI 侧可用 `BOOKSHELF_SETUP_TOKEN` / `SETUP_TOKEN` 自动透传 |
| `CHANNEL_SIGNING_SECRET` | 可选；配置后渠道头须附带 `X-Channel-Signature`（HMAC-SHA256），防伪造。CLI 侧用 `BOOKSHELF_CHANNEL_SIGNING_SECRET` 透传 |

完整示例见 `backend/.env.example`、`deploy/.env.example`。

---

## 备份

```bash
bash deploy/backup.sh
```

脚本会对 SQLite 做 `.backup` 并打包 `data/`。请按家庭习惯配置 cron / 计划任务，并视需要拷到 NAS。

若当前数据目录还没有 `covers/` / `attachments/`，脚本会跳过附件包并给出警告，但数据库备份仍会生成。

恢复前请先停服务，再替换数据库与数据目录。

---

## 安全提示

- **Owner 密码**：首次部署后必须通过 `python -m app.admin owner-init-password` 或 Web UI 初始化。密码使用 Argon2id 存储。
- **HTTPS 要求**：正式家庭数据环境的 Owner 登录、Token 签发和 Bearer 调用必须通过 HTTPS；HTTP 只能访问发现面（`/agent`、manifest 等），业务鉴权端点在 HTTP 下会拒绝。
- **Agent Token**：Token 以 `hbs_at_` 前缀格式签发，SHA-256 哈希存储，仅签发时显示一次明文。可在 Web 授权中心随时撤销。
- **Scope 限制**：每个 Agent Token 绑定特定 Scope（共 13 个），高风险操作（删除、跨成员统计）需单独授权。
- 不要把 `0.0.0.0:8000` 直接暴露到公网。
- 若 Agent/Webhook 需要公网入口，使用反向代理（Nginx/Caddy）配置 HTTPS，并限制来源。

### 安全模型迁移说明

V0.2.5 起引入 Agent Bootstrap Gateway 和 Owner 认证体系，安全模型有以下变化：

1. 移除了 `X-UI-Client: web` 头的匿名旁路（BUG-134 原修复已替换为 Owner 会话认证）
2. 移除了匿名默认成员回退
3. 所有业务端点要求认证（Bearer Token / Web 会话 / 渠道头）
4. 旧的匿名 Web/CLI 调用可能返回 `401/403`

迁移步骤：
1. 初始化 Owner 密码
2. Web UI 改用 Owner 登录会话
3. CLI/Agent 迁移到 Bearer Token 或保留渠道头认证
4. 确认所有调用方认证方式正确

---

## 下一步

- [快速开始](./get-started.md)  
- [Web UI 部署](./web-ui.md)  
- [接入 Agent](./agent-setup.md)  
- [FAQ](./faq.md)  
