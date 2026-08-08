# 部署

把 API 常驻在家庭服务器上，并做好数据备份。

---

## Docker（推荐）

```bash
cd deploy
cp .env.example .env    # 按需修改 BOOKSHELF_BIND / BOOKSHELF_DATA_DIR
docker compose up -d
docker compose ps
curl http://127.0.0.1:8000/api/v1/health
```

默认把宿主机数据目录挂到容器 `/data`（库文件、封面、附件）。

常用：

```bash
docker compose logs -f bookshelf-api
docker compose down
```

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

- API **无公网 Token 登录**，依赖局域网隔离 + IM 渠道白名单。  
- 不要把 `0.0.0.0:8000` 直接暴露到公网。  
- 若 Agent/Webhook 需要公网入口，使用反向代理或内网穿透，并限制来源。  

---

## 下一步

- [快速开始](./get-started.md)  
- [接入 Agent](./agent-setup.md)  
- [FAQ](./faq.md)  
