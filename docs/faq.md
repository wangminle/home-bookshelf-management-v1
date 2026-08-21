# FAQ

常见问题与排错。

---

## 安装与连接

### `bookshelf` 命令找不到？

```bash
cd cli && pip install -e .
which bookshelf   # Windows: where bookshelf
```

确认当前 shell 使用的是安装了 CLI 的 Python 环境。

### `doctor` / CLI 连不上 API？

```bash
export BOOKSHELF_API_URL=http://127.0.0.1:8000
bookshelf health
```

检查后端是否在跑、防火墙、以及 Docker 端口绑定（默认常为 `127.0.0.1:8000`）。

### 条码识别不可用？

需要系统 zbar + Python `pyzbar`/`Pillow`。macOS：`brew install zbar`；Debian：`apt-get install libzbar0`。Docker 镜像已含相关库。

---

## 入库与数据

### 为什么入库后没有副本？

设计如此：只有传 `--location`（或 API 带 `location`）才创建实体副本。需要时：

```bash
bookshelf add --isbn ... --location "书房"
# 或之后用 API POST /books/{id}/copies
```

### ISBN 报校验位不正确？

手工输入的 ISBN 必须通过 ISBN-10/13 校验。可核对印刷页数字，或改用书名入库。

### 重复入库会怎样？

按 ISBN / 书名+作者查重；已存在会提示已在书架，可再记购买或补副本，而不是 silently 再建一本元数据。

### 中文书元数据经常不全？

配置 `GOOGLE_BOOKS_API_KEY`；中文 `9787` ISBN 会优先走国图 NLC。仍可手工 `PATCH` 或再次入库补全。

---

## 成员与鉴权

### 先 `member` 再 `bind` 可以吗？

可以。系统还没有任何渠道绑定时，允许完成**首次初始化绑定**。白名单建立后，匿名再绑会被 `403`。

### 绑定返回 409？

同一 `(channel, external_user_id)` 不能绑到两个成员。换外部 ID，或先确认是否已绑在别人身上。

### Agent 操作一直 403？

确认：

1. 已 `bind` 成功  
2. 请求同时带了 `X-Channel` 与 `X-External-User-Id`  
3. 外部 ID 字符串完全一致  

只传一个头会得到 `400`。

如果你不是直接调 HTTP，而是通过 CLI 执行命令，也要确认运行环境里已经设置：

```bash
export BOOKSHELF_CHANNEL=feishu
export BOOKSHELF_EXTERNAL_USER_ID=ou_xxx
```

### Web UI 绑定后写操作 403？

V0.2.5 起，Web UI 使用 Owner 密码登录获取会话 Cookie，不再依赖 `X-UI-Client` 头。如果遇到 403：

1. 确认已通过 `/agent-authorization` 页面初始化 Owner 密码并登录
2. 清除浏览器 Cookie 后重新登录
3. 旧的 `X-UI-Client: web` 旁路已移除，不再有效

### Agent Token 怎么获取？

Owner 登录 `/agent-authorization` 页面后：

1. 创建 Agent 客户端
2. 创建授权（选择成员、Scope、有效期）
3. 签发 Token - **仅显示一次，请立即保存**

Token 格式为 `hbs_at_<public_id>_<secret>`，设置环境变量 `BOOKSHELF_TOKEN` 即可通过 CLI 使用。

### Token 丢失了怎么办？

Token 仅签发时显示一次。如果丢失，在授权管理页面撤销旧 Token 并重新签发即可。

### 如何撤销 Agent 授权？

在 `/agent-authorization` 或 `/agent-access` 页面：
- 撤销 Token：立即失效单个 Token
- 撤销 Grant：该授权下所有 Token 立即失效
- 撤销 Client：该客户端下所有授权和 Token 立即失效

### 如何给第二位家人绑定？

用已绑定 owner 的渠道头调用 bind，或设置 `SETUP_TOKEN` 后：

```bash
export BOOKSHELF_SETUP_TOKEN=你的口令
bookshelf bind --member-id 2 --channel feishu --external-user-id ou_yyy
```

后端需配置相同的 `SETUP_TOKEN`；CLI 会自动把 `BOOKSHELF_SETUP_TOKEN` / `SETUP_TOKEN` 透传成 `X-Setup-Token`。

---

## 部署

### 能直接暴露到公网吗？

V0.2.5 起支持完整的 Owner 认证体系（Argon2id 密码 + HTTPS Cookie 会话 + Agent Token），但仍建议使用反向代理配置 HTTPS。见 [部署 · 安全提示](./deployment.md)。

### 数据在哪？怎么备份？

默认在 `data/`（或 Docker 挂载的数据目录）。使用 `deploy/backup.sh` 定期备份。

---

## 文档与设计

### `docs/` 和 `design/` 有什么区别？

| 目录 | 内容 |
| --- | --- |
| `docs/` | 使用说明：快速开始、指南、FAQ（本文档所在处） |
| `design/` | 开发与需求：设计方案、Schema、调研 |

### 有 Web 书架页面吗？

有。二期已实现 Vue 3 SPA Web UI，提供封面墙浏览、筛选、书籍详情、阅读统计仪表盘、书架概览图生成与导出。详见 [Web UI 部署指南](./web-ui.md)。

开发模式：
```bash
cd frontend && npm run dev    # http://localhost:3000
```

生产部署：仓库根执行一键脚本，后端自动托管：
```bash
bash scripts/deploy_frontend.sh
```

---

## 仍无法解决？

1. 再跑一次 `bookshelf doctor`，保存完整输出  
2. 查看后端日志（Docker：`docker compose logs`）  
3. 对照 [`design/`](../design/) 中的设计说明与 `task-list.md` 已知问题  
