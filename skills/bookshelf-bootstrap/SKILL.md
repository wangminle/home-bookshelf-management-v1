---
name: bookshelf-bootstrap
description: Agent 引导技能。当用户说「连接书架」「bootstrap」「agent 接入」「发现能力」时使用。引导 Agent 通过发现面获取系统契约，申请授权并安装 Skills。
version: "0.2.5"
scopes: []
---

# Agent 引导（bookshelf-bootstrap）

> **Agent 首次接入时使用本技能**，完成发现 -> 授权 -> 安装 Skills 全流程。

## 适用场景

- 「我是 Agent，怎么接入家庭书架？」
- 「bootstrap / connect / 发现 API」
- 「怎么获取 Skills？」

## 流程

### Step 1: 发现

```bash
# 获取系统清单
curl https://your-bookshelf.example/agent/manifest.json

# 获取 Bootstrap Markdown
curl https://your-bookshelf.example/agent/bootstrap.md

# 获取 API 目录（RFC 9727）
curl https://your-bookshelf.example/.well-known/api-catalog
```

### Step 2: 安装 Skills

```bash
# 安装 CLI（如果尚未安装）
pip install bookshelf-cli

# 下载并安装 Skills Bundle
bookshelf skills install --from-server https://your-bookshelf.example
```

### Step 3: 申请授权

向 Owner 申请 Agent Token。Owner 通过 Web 授权中心创建授权后，你将获得：

- Token（格式：`hbs_at_xxxxxxxxxxxxxxxx_yyyyyyyyyyyyyyyy`）
- 可用 Scope 列表
- 过期时间

### Step 4: 配置并验证

```bash
# 设置 Token（通过环境变量，不要写入文件）
export BOOKSHELF_TOKEN="hbs_at_..."

# 验证连接
bookshelf auth status
bookshelf doctor --authorized
```

## 原则

1. **Token 只存环境变量** - 不写入文件、不打印到日志
2. **先发现后授权** - 通过 /agent/manifest.json 了解能力，再申请
3. **Scope 最小化** - 只申请需要的权限
4. **Token 失效时重新申请** - 不要尝试自行修复
