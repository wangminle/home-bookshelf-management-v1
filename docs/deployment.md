# 部署

把 API 常驻在家庭服务器上，并做好数据备份。

---

## Docker（推荐）

```bash
cd deploy
cp .env.example .env    # 按需修改 BOOKSHELF_BIND / BOOKSHELF_DATA_DIR
docker compose up -d
docker compose ps
# v0.3.5：/health 需 members:read；无凭证探活用 public-health
curl -f http://127.0.0.1:8000/api/v1/public-health
```

默认把宿主机数据目录挂到容器 `/data`（库文件、封面、附件）。官方 `backend/Dockerfile` 已包含前端构建阶段，`docker compose up -d --build` 即可同时得到 API 与 Web UI。

常用：

```bash
docker compose logs -f bookshelf-api
docker compose down
```

若在宿主机改前端后要热更新进已有数据卷以外的镜像，重新 `--build`；lwa 路径请用下面的升级步骤，不要只跑 `lwa rebuild`。

---

## lwa 本地部署（家庭服务器推荐）

[lwa（Local Webpage Access）](https://github.com/wangminle/local-webpage-access) 是轻量本地网页部署工具，自动生成 Dockerfile/compose 并管理端口。适合家庭服务器一键部署。

`lwa rebuild` **不会**同步 `Downloads/` 下的最新源码，也**不会**构建前端。升级必须按下面做。

### lwa 本地部署/升级

1. 把最新源码同步到 `local-webpage-access/apps/home-bookshelf-management-v1/current/`（或你的实例 `current/`）。
2. 在仓库根（即 `current/`）构建并同步前端与 Skills bundle（别名按实际修改）：

```bash
bash scripts/deploy_frontend.sh --base /home-bookshelf/
```

3. 再执行 `lwa rebuild home-bookshelf-management-v1`。
4. 探活：

```bash
curl -f http://<host>:<port>/api/v1/public-health
```

不要再用 `GET /api/v1/health` 做无凭证探活（v0.3.5 起需 `members:read`，会 401）。

首次导入：

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

Skills bundle 由 `scripts/deploy_frontend.sh` 一并构建到 `backend/static/skills/`（避开 lwa `.dockerignore` 的 `**/dist`），随 `backend/` 目录被 `lwa import` 携带。注意：lwa 导入的只有 `backend/`，容器内**没有** `skills/` 与 `scripts/` 源文件，「进容器手工跑构建脚本」不可行；应用启动时的自动兜底构建只在包含源码的环境（官方 Docker 镜像、本机源码运行）生效。

### lwa 网关后初始化 Owner 密码

升级场景（库里已有 Owner 成员但没设密码）下，`POST /auth/init-password` 要求请求来自本机（loopback）或带 `X-Setup-Token`。经 lwa 网关反代后，后端看到的对端是 Docker 网关 IP 而非真实客户端，loopback 判定会失效（GitHub #8）。两种解法二选一：

1. **配置 `SETUP_TOKEN`**（推荐，最简单），然后：

```bash
# 注意路径：web_auth 挂在 /auth 下，不在 /api/v1 下
curl -X POST http://<host>:<port>/home-bookshelf/auth/init-password \
  -H "Content-Type: application/json" \
  -H "X-Setup-Token: <SETUP_TOKEN>" \
  -d '{"password": "<新密码>", "confirm": "<新密码>"}'
```

2. **配置 `TRUSTED_PROXIES`**（逗号分隔的 IP/CIDR，指向 lwa 网关/容器网段，如 `TRUSTED_PROXIES=172.16.0.0/12`）。配置后后端信任网关门送来的 `X-Forwarded-For`，从网关所在服务器本机（`127.0.0.1`）访问 Web UI 初始化页即可直接设置密码。未配置时 XFF 一律不可信，不影响安全性。
> BUG-181（GitHub #10）：后端按**右值法**解析 X-Forwarded-For（从右跳过可信代理后取第一个非可信地址），网关无论追加还是覆盖 XFF 均安全。仍建议网关直接覆盖：`proxy_set_header X-Forwarded-For $remote_addr;`，杜绝首跳伪造空间。

> 路径易错点：正确是 `<别名>/auth/init-password`；误写成 `<别名>/api/v1/auth/init-password` 会返回 405。

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
- **HTTPS 建议**：正式家庭数据环境的 Owner 登录、Token 签发和 Bearer 调用建议走 HTTPS。后端不强制拒绝 HTTP——HTTP 下功能均可用，区别仅在 HTTPS（含反向代理 `X-Forwarded-Proto: https`）时会话 Cookie 带 `Secure` 标志，HTTP 下不带。
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
5. **v0.3.5**：`GET /api/v1/health` 改为需 `members:read`。无凭证探活、Docker/lwa 健康检查请改用：

```bash
curl -f http://<host>:<port>/api/v1/public-health
```

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
