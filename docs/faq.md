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

### 如何给第二位家人绑定？

用已绑定 owner 的渠道头调用 bind，或设置 `SETUP_TOKEN` 后：

```bash
export BOOKSHELF_SETUP_TOKEN=你的口令
bookshelf bind --member-id 2 --channel feishu --external-user-id ou_yyy
```

后端需配置相同的 `SETUP_TOKEN`。

---

## 部署

### 能直接暴露到公网吗？

不建议。一期依赖局域网 + 渠道白名单，没有完整的公网登录体系。见 [部署 · 安全提示](./deployment.md)。

### 数据在哪？怎么备份？

默认在 `data/`（或 Docker 挂载的数据目录）。使用 `deploy/backup.sh` 定期备份。

---

## 文档与设计

### `docs/` 和 `design/` 有什么区别？

| 目录 | 内容 |
| --- | --- |
| `docs/` | 使用说明：快速开始、指南、FAQ（本文档所在处） |
| `design/` | 开发与需求：设计方案、Schema、调研 |

### 还要 Web 书架吗？

二期规划，见 `design/` 与 `task-list.md` 中的 PLN 条目。一期以 CLI + Agent 为主。

---

## 仍无法解决？

1. 再跑一次 `bookshelf doctor`，保存完整输出  
2. 查看后端日志（Docker：`docker compose logs`）  
3. 对照 [`design/`](../design/) 中的设计说明与 `task-list.md` 已知问题  
