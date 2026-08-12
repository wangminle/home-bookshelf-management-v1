"""WBS-2：Agent 发现面服务层。

从安全配置生成非业务元数据，绝不泄露书籍/成员/统计等数据。
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.schemas.agent_discovery import (
    Capability,
    DataPolicy,
    Linkset,
    LinksetAnchor,
    LinksetEntry,
    Manifest,
    ManifestLinks,
    PublicHealthData,
    ServiceInfo,
    SkillIndex,
    SkillIndexEntry,
    SkillsRef,
)

_APP_VERSION = "0.2.4"

# WBS-0：公开能力目录--只描述"系统能做什么"，不包含业务数据。
_CAPABILITIES = [
    Capability(id="books.search", description="搜索用户获权范围内的藏书", authorization_required=True, required_scopes=["books:read"], risk="read"),
    Capability(id="books.intake", description="向用户获权的家庭书架新增图书", authorization_required=True, required_scopes=["books:write"], risk="write"),
    Capability(id="books.edit", description="编辑书目信息", authorization_required=True, required_scopes=["books:write"], risk="write"),
    Capability(id="books.delete", description="删除或合并书籍", authorization_required=True, required_scopes=["books:delete"], risk="delete"),
    Capability(id="reading.progress", description="查看和更新阅读进度", authorization_required=True, required_scopes=["reading:read", "reading:write"], risk="write"),
    Capability(id="reading.logs", description="记录阅读日志", authorization_required=True, required_scopes=["reading:write"], risk="write"),
    Capability(id="notes.manage", description="管理读书笔记与附件", authorization_required=True, required_scopes=["notes:read", "notes:write"], risk="write"),
    Capability(id="purchases.manage", description="记录购买信息", authorization_required=True, required_scopes=["purchases:read", "purchases:write"], risk="write"),
    Capability(id="stats.view", description="查看授权成员统计", authorization_required=True, required_scopes=["stats:read"], risk="read"),
    Capability(id="files.download", description="下载授权范围附件", authorization_required=True, required_scopes=["files:read"], risk="read"),
]


def get_base_url() -> str:
    """获取公开 Base URL。优先使用配置的 PUBLIC_BASE_URL。

    WBS-1：不得无条件信任请求 Host 头。
    """
    if settings.public_base_url:
        return settings.public_base_url
    # 未配置时使用空串，前端用相对路径
    return ""


def build_manifest() -> Manifest:
    base = get_base_url()
    return Manifest(
        service=ServiceInfo(
            name="家庭图书管理系统",
            version=_APP_VERSION,
            description="自托管家庭藏书、阅读与笔记管理服务",
        ),
        links=ManifestLinks(
            human_entry="/agent",
            agent_guide="/agent/bootstrap.md",
            api_catalog="/.well-known/api-catalog",
            openapi="/agent/openapi.json",
            skills_index="/agent/skills/index.json",
            authorization_manage="/settings/agent-access",
        ),
        data_policy=DataPolicy(),
        capabilities=_CAPABILITIES,
        skills=SkillsRef(
            bundle_version=_get_skills_bundle_version(),
            index="/agent/skills/index.json",
        ),
    )


def build_linkset() -> Linkset:
    base = get_base_url()
    return Linkset(
        linkset=[
            LinksetAnchor(
                anchor=f"{base}/" if base else "/",
                service_desc=[
                    LinksetEntry(
                        href=f"{base}/agent/openapi.json" if base else "/agent/openapi.json",
                        type="application/vnd.oai.openapi+json",
                    )
                ],
                describedby=[
                    LinksetEntry(
                        href=f"{base}/agent/bootstrap.md" if base else "/agent/bootstrap.md",
                        type="text/markdown",
                    )
                ],
            )
        ]
    )


def build_bootstrap_md() -> str:
    """Agent 可读的初始化说明 Markdown。"""
    base = get_base_url()
    base_prefix = base if base else ""
    return f"""# 家庭图书管理系统 - Agent 引导

> 本页面面向 AI Agent。人类用户请访问 {base_prefix}/agent。

## 系统简介

家庭图书管理系统是一个自托管的藏书、阅读与笔记管理服务。
本入口帮助你了解系统能力、安装 Skills 并申请数据访问授权。

**重要：此入口不会提供任何家庭书架数据。** 所有业务数据访问需要用户明确授权。

## 能力目录

| 能力 | 说明 | 所需权限 | 风险 |
| --- | --- | --- | --- |
| books.search | 搜索藏书 | books:read | 读 |
| books.intake | 新增图书 | books:write | 写 |
| books.edit | 编辑书目 | books:write | 写 |
| books.delete | 删除书籍 | books:delete | 删 |
| reading.progress | 阅读进度 | reading:read/write | 写 |
| reading.logs | 阅读日志 | reading:write | 写 |
| notes.manage | 笔记附件 | notes:read/write | 写 |
| purchases.manage | 购买记录 | purchases:read/write | 写 |
| stats.view | 统计 | stats:read | 读 |
| files.download | 附件下载 | files:read | 读 |

## 接口发现

- 机器清单: {base_prefix}/agent/manifest.json
- API 规范: {base_prefix}/agent/openapi.json
- API Catalog: {base_prefix}/.well-known/api-catalog
- Skills 索引: {base_prefix}/agent/skills/index.json

## 授权流程

1. 读取本说明和 manifest.json 了解系统能力。
2. 确定所需 Scope，向用户说明理由。
3. 引导用户打开 Web 授权中心创建授权。
4. 用户生成 Token 后配置到你的环境变量。
5. 使用 `Authorization: Bearer <token>` 调用业务 API。

**未经授权调用业务端点将返回 401。**

## 未授权时可以做什么

- 读取本说明和 manifest.json
- 下载 Skills 包
- 检查 API 兼容性
- 不可读取任何业务数据
- 不可创建默认成员
- 不可自动绑定渠道
"""


def build_public_health() -> PublicHealthData:
    return PublicHealthData(
        status="available",
        service="home-bookshelf",
        authorization_required=True,
    )


def build_agent_openapi() -> dict:
    """从 allowlist 生成 Agent 专用 OpenAPI，只描述允许 Agent 调用的业务动作。

    不包含管理端点、数据库模型、内部字段。
    """
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Home Bookshelf Agent API",
            "version": _APP_VERSION,
            "description": "Agent 可调用的业务 API 子集。所有端点需要 Bearer Token 授权。",
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "使用 Authorization: Bearer <token>",
                }
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/api/v1/books": {
                "get": {
                    "summary": "搜索藏书",
                    "description": "搜索用户获权范围内的藏书",
                    "tags": ["books"],
                    "security": [{"bearerAuth": []}],
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "搜索关键词"},
                        {"name": "status", "in": "query", "schema": {"type": "string"}, "description": "阅读状态筛选"},
                    ],
                    "responses": {
                        "200": {"description": "搜索结果"},
                        "401": {"description": "未提供有效凭证"},
                        "403": {"description": "缺少所需 Scope"},
                    },
                },
                "post": {
                    "summary": "新增图书",
                    "tags": ["books"],
                    "security": [{"bearerAuth": []}],
                    "responses": {
                        "201": {"description": "创建成功"},
                        "401": {"description": "未授权"},
                        "403": {"description": "缺少 books:write"},
                    },
                },
            },
            "/api/v1/books/{book_id}": {
                "get": {"summary": "获取书目详情", "tags": ["books"], "security": [{"bearerAuth": []}]},
                "patch": {"summary": "编辑书目", "tags": ["books"], "security": [{"bearerAuth": []}]},
                "delete": {"summary": "删除书目", "tags": ["books"], "security": [{"bearerAuth": []}]},
            },
            "/api/v1/books/{book_id}/progress": {
                "post": {"summary": "更新阅读进度", "tags": ["reading"], "security": [{"bearerAuth": []}]},
            },
            "/api/v1/books/{book_id}/notes": {
                "post": {"summary": "新建笔记", "tags": ["notes"], "security": [{"bearerAuth": []}]},
            },
            "/api/v1/books/intake/json": {
                "post": {"summary": "JSON 入库", "tags": ["books"], "security": [{"bearerAuth": []}]},
            },
            "/api/v1/stats": {
                "get": {"summary": "查看统计", "tags": ["stats"], "security": [{"bearerAuth": []}]},
            },
        },
    }


def _get_skills_bundle_version() -> str:
    """从 dist/skills/manifest.json 读取 bundle 版本（单一事实来源）。

    BUG-158/CHK-048：原硬编码 '2026.08.11.1' 与实际产物 skills-0.2.4.zip
    不一致，导致 /agent/skills/download/2026.08.11.1.zip 返回 404。
    现从 build_skills_bundle.py 生成的 manifest.json 读取版本号，
    与 skill_catalog.BUNDLE_DIR 中的实际 ZIP 文件名保持一致。
    """
    import json

    from app.services import skill_catalog

    manifest_path = skill_catalog.BUNDLE_DIR / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            version = data.get("version")
            if version:
                return version
        except (json.JSONDecodeError, OSError):
            pass

    # 回退：与 skill_catalog._SKILLS_VERSION 一致
    return skill_catalog._SKILLS_VERSION


def build_skills_index() -> SkillIndex:
    """构建 Skills 索引（WBS-4: 从 skill_catalog 获取真实数据）。"""
    from app.services import skill_catalog

    bundle_version = _get_skills_bundle_version()
    entries: list[SkillIndexEntry] = []

    # 尝试获取 SHA256 和大小
    sha256 = ""
    size_bytes = 0
    try:
        sha256 = skill_catalog.get_bundle_sha256(bundle_version)
        from pathlib import Path as _Path
        bundle_path = skill_catalog.BUNDLE_DIR / f"skills-{bundle_version}.zip"
        if bundle_path.is_file():
            size_bytes = bundle_path.stat().st_size
    except Exception:
        pass  # bundle 未构建时使用空值

    for skill in skill_catalog.list_skills():
        entries.append(
            SkillIndexEntry(
                name=skill.name,
                version=skill.version,
                description=skill.description,
                archive_url=f"/agent/skills/download/{bundle_version}.zip",
                sha256=sha256,
                size_bytes=size_bytes,
                requested_scopes=skill.scopes,
                writes_data=any(s.endswith(":write") or s.endswith(":delete") for s in skill.scopes),
            )
        )

    return SkillIndex(bundle_version=bundle_version, skills=entries)


def build_llms_txt() -> str:
    """精简文档导航，兼容 llms.txt 提案。"""
    base = get_base_url()
    base_prefix = base if base else ""
    return f"""# 家庭图书管理系统

> 自托管家庭藏书、阅读与笔记管理服务

## 概述
家庭图书管理系统帮助家庭管理实体藏书、阅读进度、笔记和购买记录。
Agent 可以通过 REST API 操作，但所有业务数据访问需要用户明确授权。

## API
- Agent 引导: {base_prefix}/agent/bootstrap.md
- 机器清单: {base_prefix}/agent/manifest.json
- API 规范: {base_prefix}/agent/openapi.json
- Skills 索引: {base_prefix}/agent/skills/index.json

## 授权
所有业务端点需要 Bearer Token。
用户在 Web 授权中心创建限权 Token 后配置到 Agent 环境变量。

## 可选
- CLI 工具: bookshelf bootstrap <url>
- Skills 安装: gh skill install <目录> --from-local
"""
