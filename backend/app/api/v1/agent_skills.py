"""WBS-4：Skills 分发 API。

端点：
- GET /agent/skills/index.json       - Skills 索引
- GET /agent/skills/download/{version}.zip - 下载 Bundle
- GET /agent/skills/SHA256SUMS       - 校验和文件
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services import skill_catalog

router = APIRouter(tags=["skills-distribution"])


@router.get("/agent/skills/index.json")
def skills_index():
    """Skills 索引（公开发现面）。

    BUG-158/CHK-048：返回 agent_discovery 的完整 SkillIndex（含 bundle_version、
    archive_url、sha256），使 Agent 能直接构造下载 URL 并验证完整性。
    原先调用 skill_catalog.build_skills_index() 只返回简单字典，缺少下载元数据。
    """
    from app.services.agent_discovery import build_skills_index

    index = build_skills_index()
    return JSONResponse(
        index.model_dump(),
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/agent/skills/download/{version}.zip")
def download_skills_bundle(version: str):
    """下载 Skills Bundle（不可变，缓存头）。"""
    return skill_catalog.serve_bundle(version)


@router.get("/agent/skills/SHA256SUMS")
def skills_sha256sums():
    """SHA256SUMS 文件。"""
    from pathlib import Path
    sums_path = skill_catalog.BUNDLE_DIR / "SHA256SUMS"
    if not sums_path.is_file():
        return PlainTextResponse("", status_code=404)
    return PlainTextResponse(
        sums_path.read_text(encoding="utf-8"),
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=300"},
    )
