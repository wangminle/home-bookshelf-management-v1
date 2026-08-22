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
| `ANONYMOUS_CATALOG_MODE` | 匿名共享书架（C 模式）：`lan_shared` 开启 / `disabled` 关闭（代码默认；存量部署升级不改变现状，新部署在 deploy 模板引导下开启） |
| `TRUSTED_LAN_CIDRS` | 可信家庭局域网网段（逗号分隔 CIDR，如 `192.168.1.0/24`）。匿名浏览只对回环、该列表内来源（或经 `TRUSTED_PROXIES` 还原后落在列表内）开放 |
| `PUBLIC_CATALOG_RATE_LIMIT_PER_MINUTE` | 匿名书目接口每客户端 IP 每分钟请求上限（默认 60） |
| `PUBLIC_CATALOG_MAX_PAGE_SIZE` | 匿名书目接口单页最大条数（默认 50） |

完整示例见 `backend/.env.example`、`deploy/.env.example`。

## 匿名共享书架（C 模式）

权限阶段 1 起，可信家庭局域网内的访客无需登录即可在 `/shared` 浏览脱敏书目
（书名、作者、出版社、分类、简介、公共标签、封面缩略图、在架/外借状态）；
阅读进度、笔记、购买、成员与位置信息永不匿名展示，完整业务 API 仍要求登录。

开启步骤：

1. 确认家庭网段（如 `192.168.1.0/24`）；
2. 设置 `ANONYMOUS_CATALOG_MODE=lan_shared` 与 `TRUSTED_LAN_CIDRS=192.168.1.0/24`；
3. 重启后端；`bookshelf doctor` 会检查"lan_shared 已开启但未配置可信 CIDR"等不一致。

行为说明：

- 反向代理（lwa/nginx）后部署时，需同时配置 `TRUSTED_PROXIES`，系统按右值法
  从 `X-Forwarded-For` 还原真实客户端地址再判定是否可信；
- 无法确认请求来自可信局域网时，匿名接口自动降级（HTTP 403 `LAN_REQUIRED`），
  前端显示登录入口；随时可把 `ANONYMOUS_CATALOG_MODE` 改回 `disabled` 立即关闭，
  不改写任何书目数据。

## 逐书可见级别与 B 模式（权限阶段 4）

每本书有匿名可见级别：`lan_shared`（默认，兼容存量未标记）/ `public` /
`members_only` / `private`。匿名书架按系统模式过滤：

- `ANONYMOUS_CATALOG_MODE=lan_shared`（C 模式）：可见 lan_shared + public；
- `ANONYMOUS_CATALOG_MODE=explicit_public`（B 模式）：仅 public——切换前
  建议在 Web「策略」页用 C→B 预览确认哪些书会从匿名书架消失；
- `disabled`：全关。

Owner 在书籍详情页可单书设置可见级别，在「策略」页（`/catalog-policy`）可
批量设置并查看切换预览。切换模式 = 修改环境配置并重启；回滚 = 切回原值，
任何情况下私有记录不会意外公开。

---
## MCP 只读试点（并行轨，默认关闭）

`/mcp` 提供两个只读工具（`bookshelf_search_books` / `bookshelf_get_book`），
面向支持 MCP 的 Agent 客户端。启用步骤：

1. 生成独立高熵游标密钥：`openssl rand -hex 32` → `MCP_CURSOR_SIGNING_SECRET`
   （长度至少 32 字符，不得复用 Agent Token、Owner 密码或渠道签名密钥；过短或复用会在启动时报错拒绝服务）；
2. 在前端「Agent 授权」页注册客户端并创建**专用只读 Grant**：Scope 仅
   `books:read` **且显式声明数据范围 `household_shared`**，建议 30 天，签发
   Token 交由 Agent 客户端保管——旧语义 Grant（未声明数据范围）会被 403
   `PILOT_GRANT_REQUIRED` 拒绝，禁止旧 Grant 自动进入 MCP；
3. 非回环部署配置 `MCP_ALLOWED_HOSTS`（Host 校验，默认仅内置回环精确值，
   不匹配 421）；浏览器跨域客户端另配 `MCP_TRUSTED_ORIGINS`（Origin 精确
   匹配，不可信 403）；
4. 配置源地址信任边界 `MCP_TRUSTED_CIDRS`（如 `192.168.1.0/24`）：仅信任
   网段内的直连客户端、或经 `TRUSTED_PROXIES` 可信代理（按右值法解析
   `X-Forwarded-For`）还原后仍位于信任网段的请求可进入鉴权，其余来源
   403 `NETWORK_DENIED`；默认同时要求 HTTPS（`MCP_REQUIRE_HTTPS=true`，
   回环豁免；可信代理链路读取 `X-Forwarded-Proto`，缺失按明文拒绝），
   家庭内网明文试点需显式设 `MCP_REQUIRE_HTTPS=false`；
5. 设置 `MCP_ENABLED=true` 重启后端；`/mcp` 只接受
   `Authorization: Bearer <token>` + 必填的 `MCP-Protocol-Version` 头
   （allowlist 仅 `2026-07-28`），Cookie/渠道头/匿名一律 401；每个请求的
   `params._meta` 必须为对象，网关路由头 `Mcp-Method`/`Mcp-Name`（如携带）
   必须与请求体方法/工具名一致，否则 400。

行为要点：

- 握手用 `server/discover`（该协议版本已移除 `initialize`）；数据范围由 Grant
  显式声明且服务端固定为家庭共享书目（L1/L2 白名单字段），不含成员、阅读、
  笔记、购买、封面 URL 或文件路径；
- 搜索必须至少带一个筛选条件（纯空白不计）；单页最多 20 条（配置超限自动
  夹取到 20）；游标经 HMAC 签名防篡改且限长；
- 限流两层：每个 Agent Client + Grant 共享全局每分钟额度（未知方法同样
  计入，无法借换方法名绕过），单个工具另有子额度；Grant 撤销/过期后下一
  请求立即 401，Scope 变更会递增 Grant 版本并撤销全部旧 Token（需重签）；
- 请求/响应体各限 1 MiB（`MCP_MAX_REQUEST_BODY_BYTES` /
  `MCP_MAX_RESPONSE_BODY_BYTES`），请求超限 413、响应超限拒绝下发；
- 全部调用进入共享安全审计（拒绝必记、工具调用放行逐次记录；审计写库
  失败时返回 503 拒绝服务，绝不放行数据）；
- 可选封面 Resource（`resources/read`，URI `bookshelf://covers/{id}`）默认关闭：
  `MCP_COVER_RESOURCE_ENABLED=true` 启用，同样受试点 Grant/限流/审计门禁，
  返回 base64 blob（上限 `MCP_COVER_MAX_BYTES`，默认 512 KiB），不复用匿名
  封面 URL；实机验证前建议保持关闭；
- 关闭：`MCP_ENABLED=false` 重启即可，不影响 REST/Web/CLI。

---

## 成员账号管理（权限阶段 2）

Owner 与家庭成员使用统一登录页 `/login`（用户名 + 密码；系统只有一条凭据时
用户名可留空）。成员账号由 Owner 管理：

- **创建成员并设密码**：`POST /members` 建成员 → `POST /members/{id}/password`
  设置初始密码（登录用户名默认按成员显示名生成，响应中返回）；
- **停用/恢复与角色调整**：`PATCH /members/{id}`（`{"disabled": true}` 或
  `{"role": "member"}`）；变更后该成员全部会话立即失效；唯一活跃 owner 不可
  停用或降级；
- **重置密码**：`POST /members/{id}/password`（重置后该成员全部会话失效）；
- **自助改密**：登录后 `POST /auth/change-password`（保留当前会话，其余失效）；
- 连续 5 次密码错误锁定 15 分钟；登录接口按来源 IP 限流失败尝试。

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
