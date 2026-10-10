# 快速开始

从安装到第一次成功入库。大约 10 分钟。

## 你将完成

1. 启动后端 API  
2. 安装 `bookshelf` CLI  
3. 用 `doctor` 自检  
4. 用 ISBN 或书名入库一本书  

---

## 1. 启动后端

在仓库根目录：

```bash
cd backend
bash install.sh          # Windows: install.bat
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir .
```

浏览器打开：`http://127.0.0.1:8000/docs`，能看到 Swagger 即表示服务正常。

可选：配置 Google Books（提升命中率）

```bash
# backend/.env
GOOGLE_BOOKS_API_KEY=你的密钥
```

条码识别需要系统安装 zbar：

- macOS：`brew install zbar`
- Debian/Ubuntu：`apt-get install libzbar0`

### 启动 Web UI（可选）

```bash
cd frontend
npm install
npm run dev          # 开发模式，http://localhost:3000，/api、/auth、/agent-access 自动代理到 :8000
                     # （/agent、/skills、/mcp 等其余根级路由 dev 模式不代理，见 web-ui.md）
```

生产部署时构建前端并拷入后端，由后端统一托管：

```bash
bash scripts/deploy_frontend.sh
# 重启后端后访问 http://127.0.0.1:8000/ 即为 Web UI
```

详见 [Web UI 部署指南](./web-ui.md)。

---

## 2. 安装 CLI

```bash
cd cli
pip install -e .
bookshelf --help
```

远程服务器时指定 API 地址：

```bash
export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000
```

若你希望 CLI 以后直接按某个已绑定成员身份写入，也可以在本机长期保留：

```bash
export BOOKSHELF_CHANNEL=feishu
export BOOKSHELF_EXTERNAL_USER_ID=ou_xxxxxxxx
```

---

## 3. 自检

```bash
bookshelf doctor
```

全部通过后再继续。注意：业务端点（含读命令）均需认证——无凭证将得到 401。请先在前端「Agent 授权」页签发 Token 并 `export BOOKSHELF_TOKEN=hbs_at_...`（流程见 `docs/agent-setup.md`），或使用下方第 5 节的渠道绑定身份。

---

## 4. 第一次入库

**方式 A：ISBN**

```bash
bookshelf add --isbn 9787020008735 --no-json
```

**方式 B：书名**

```bash
bookshelf add --title "活着" --author "余华" --no-json
```

**方式 C：书封/条码照片**

```bash
bookshelf add --image ./cover.jpg --no-json
```

成功后会打印书名与 ID。再用：

```bash
bookshelf find --keyword "活着" --no-json
bookshelf show --id <书ID> --no-json
```

---

## 5.（推荐）绑定 IM 渠道（白名单）

若要通过飞书等 Agent 操作，需要白名单：

```bash
# 空库引导：直接绑定即可，首个 bind（member_id=1）会自动创建默认 owner
bookshelf bind --member-id 1 --channel feishu --external-user-id ou_xxxxxxxx
```

注意：库非空后（例如先用 `bookshelf member` 建过成员）匿名 `bind` 会被拒绝（403）。此时需设置 `BOOKSHELF_SETUP_TOKEN`（CLI 以 `X-Setup-Token` 头透传，即部署环境的 `SETUP_TOKEN`），或改用已绑定成员的身份（`BOOKSHELF_CHANNEL`/`BOOKSHELF_EXTERNAL_USER_ID`）再绑定。因此空库引导推荐直接用上面的 bind 命令，不要先建成员。

绑定完成后，若你平时通过 CLI 或 Agent+CLI 直接写入，可设置：

```bash
export BOOKSHELF_CHANNEL=feishu
export BOOKSHELF_EXTERNAL_USER_ID=ou_xxxxxxxx
```

这样 `add` / `progress` / `purchase` / `note` / `reading-log` 等命令都会自动带上绑定身份。

---

## 下一步

- 日常用法 → [使用指南](./user-guide.md)  
- 命令一览 → [CLI 参考](./cli-reference.md)  
- 拍照批量入库 → [批量导入](./batch-import.md) 与 Web UI「批量入库」页  
- 常驻部署 → [部署](./deployment.md)  
- 接 Agent → [接入 Agent](./agent-setup.md)  
- 卡住了 → [FAQ](./faq.md)  
