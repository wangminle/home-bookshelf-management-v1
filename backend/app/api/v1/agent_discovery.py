"""WBS-2：Agent 公开发现面路由。

所有路由允许局域网匿名 GET/HEAD/OPTIONS，只返回非业务元数据。
路由注册顺序早于 SPA fallback，机器契约由后端显式控制。

Skills 索引和下载由 WBS-4 的 agent_skills.py 提供。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from app.schemas.agent_discovery import Manifest
from app.services.agent_discovery import (
    build_agent_openapi,
    build_bootstrap_md,
    build_linkset,
    build_llms_txt,
    build_manifest,
    build_public_health,
)

discovery_router = APIRouter()


@discovery_router.get("/agent/manifest.json", response_model=Manifest)
def get_manifest() -> Manifest:
    """项目机器清单。不包含业务数据。"""
    return build_manifest()


@discovery_router.get("/agent/bootstrap.md")
def get_bootstrap() -> Response:
    """Agent 可读初始化说明（Markdown）。"""
    return Response(
        content=build_bootstrap_md(),
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@discovery_router.get(
    "/.well-known/api-catalog",
    response_class=JSONResponse,
    responses={200: {"content": {"application/linkset+json": {}}}},
)
def get_api_catalog() -> JSONResponse:
    """RFC 9727 API Catalog Linkset。"""
    linkset = build_linkset()
    return JSONResponse(
        content=linkset.model_dump(),
        media_type="application/linkset+json",
        headers={"Cache-Control": "public, max-age=300"},
    )


@discovery_router.get("/llms.txt")
def get_llms_txt() -> Response:
    """精简文档导航。"""
    return Response(
        content=build_llms_txt(),
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300"},
    )


@discovery_router.get("/agent/openapi.json")
def get_agent_openapi() -> JSONResponse:
    """经 allowlist 过滤的 Agent API 结构。不包含管理端点和内部模型。"""
    spec = build_agent_openapi()
    return JSONResponse(
        content=spec,
        headers={"Cache-Control": "public, max-age=300"},
    )
