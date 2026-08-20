"""WBS-4：Skills 目录服务。

管理 Skills 索引、版本列表和 bundle 下载。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.responses import FileResponse

# Skills 目录路径
# 开发环境：从 __file__ 向上查找仓库根（含 skills/ 目录）。
# Docker 环境：skills/ 被 COPY 到 /app/skills/，从 __file__ 向上一级即可找到。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if not (_PROJECT_ROOT / "skills").is_dir():
    # 开发环境：再上一级到仓库根
    _PROJECT_ROOT = _PROJECT_ROOT.parent
if not (_PROJECT_ROOT / "skills").is_dir():
    # 回退：原始计算（四级向上）
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILLS_DIR = _PROJECT_ROOT / "skills"

# 已发布的 bundle 目录
BUNDLE_DIR = _PROJECT_ROOT / "dist" / "skills"

# 版本号
_SKILLS_VERSION = "0.2.5"


@dataclass
class SkillInfo:
    name: str
    description: str
    scopes: list[str]
    version: str


def _parse_skill_md(md_path: Path) -> SkillInfo | None:
    """解析 SKILL.md 的 frontmatter。"""
    try:
        content = md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    # 解析 frontmatter
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_text = parts[1].strip()
    name = md_path.parent.name
    description = ""
    scopes: list[str] = []
    version = _SKILLS_VERSION

    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("scopes:"):
            scopes_str = line.split(":", 1)[1].strip()
            # 解析 [scope1, scope2] 或 scope1, scope2
            scopes_str = scopes_str.strip("[]")
            scopes = [s.strip().strip('"').strip("'") for s in scopes_str.split(",") if s.strip()]
        elif line.startswith("version:"):
            version = line.split(":", 1)[1].strip().strip('"').strip("'")

    return SkillInfo(name=name, description=description, scopes=scopes, version=version)


def list_skills() -> list[SkillInfo]:
    """列出所有可用 Skills。"""
    skills: list[SkillInfo] = []
    if not SKILLS_DIR.is_dir():
        return skills
    for child in sorted(SKILLS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        md = child / "SKILL.md"
        if not md.is_file():
            continue
        info = _parse_skill_md(md)
        if info is not None:
            skills.append(info)
    return skills


def build_skills_index() -> dict:
    """构建 Skills 索引 JSON。"""
    skills = list_skills()
    return {
        "schema_version": "1.0",
        "service": "home-bookshelf",
        "version": _SKILLS_VERSION,
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "scopes": s.scopes,
                "version": s.version,
            }
            for s in skills
        ],
    }


def get_bundle_path(version: str) -> Path:
    """获取指定版本的 bundle 路径。"""
    # 安全检查：版本号只允许字母数字和点
    if not all(c.isalnum() or c in ".-" for c in version):
        raise HTTPException(status_code=400, detail="无效的版本号")

    bundle_path = BUNDLE_DIR / f"skills-{version}.zip"
    if not bundle_path.is_file():
        raise HTTPException(status_code=404, detail=f"Skills bundle v{version} 不存在")

    # 路径穿越防护
    resolved = bundle_path.resolve()
    if not resolved.is_relative_to(BUNDLE_DIR.resolve()):
        raise HTTPException(status_code=400, detail="路径越界")

    return bundle_path


def get_bundle_sha256(version: str) -> str:
    """获取指定版本 bundle 的 SHA256。"""
    bundle_path = get_bundle_path(version)
    sums_path = BUNDLE_DIR / "SHA256SUMS"
    if sums_path.is_file():
        for line in sums_path.read_text(encoding="utf-8").splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip() == bundle_path.name:
                return parts[0].strip()
    # 回退：实时计算
    return hashlib.sha256(bundle_path.read_bytes()).hexdigest()


def serve_bundle(version: str) -> FileResponse:
    """下载指定版本的 bundle。"""
    bundle_path = get_bundle_path(version)
    sha256 = get_bundle_sha256(version)
    return FileResponse(
        path=str(bundle_path),
        media_type="application/zip",
        filename=f"skills-{version}.zip",
        headers={
            "X-SHA256": sha256,
            "X-Bundle-Version": version,
            "Cache-Control": "public, max-age=3600, immutable",
        },
    )
