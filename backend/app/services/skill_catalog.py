"""WBS-4：Skills 目录服务。

管理 Skills 索引、版本列表和 bundle 下载。
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.responses import FileResponse

# Skills 目录路径
# 开发环境：从 __file__ 向上查找仓库根（含 skills/ 目录）。
# Docker 环境：skills/ 被 COPY 到 /app/skills/，从 __file__ 向上一级即可找到。
_APP_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _APP_ROOT
if not (_PROJECT_ROOT / "skills").is_dir():
    # 开发环境：再上一级到仓库根
    _PROJECT_ROOT = _PROJECT_ROOT.parent
if not (_PROJECT_ROOT / "skills").is_dir():
    # 回退：原始计算（四级向上）
    _PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = _PROJECT_ROOT / "skills"

_log = logging.getLogger(__name__)


def _bundle_has_artifact(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    if (directory / "manifest.json").is_file():
        return True
    return any(directory.glob("skills-*.zip"))


def resolve_bundle_dir(project_root: Path | None = None) -> Path:
    """GitHub #5：优先 backend/static/skills（不被 lwa **/dist ignore），兼容 Docker /app/static 与旧 dist/skills。

    默认根取 _APP_ROOT（仓库内 backend/、容器内 /app），不依赖 skills/ 源目录存在：
    lwa 容器只有 backend/ 内容，按 skills/ 探测根会退化到 /，找不到随 static/ 携带的预构建 bundle。
    """
    env = os.environ.get("SKILLS_BUNDLE_DIR")
    if env:
        return Path(env)
    root = project_root if project_root is not None else _APP_ROOT
    if (root / "backend" / "app").is_dir():
        preferred = root / "backend" / "static" / "skills"
    else:
        preferred = root / "static" / "skills"
    legacy = root / "dist" / "skills"
    if _bundle_has_artifact(legacy) and not _bundle_has_artifact(preferred):
        return legacy
    return preferred


# 已发布的 bundle 目录
BUNDLE_DIR = resolve_bundle_dir()

# 版本号
_SKILLS_VERSION = "0.2.5"


@dataclass
class SkillInfo:
    name: str
    description: str
    scopes: list[str]
    version: str


def _parse_frontmatter(content: str, name: str) -> SkillInfo | None:
    """解析 SKILL.md frontmatter 文本（源码文件与 bundle 内条目共用）。"""
    # 解析 frontmatter
    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_text = parts[1].strip()
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


def _parse_skill_md(md_path: Path) -> SkillInfo | None:
    """解析 SKILL.md 的 frontmatter。"""
    try:
        content = md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return _parse_frontmatter(content, md_path.parent.name)


def _skills_from_bundle() -> list[SkillInfo]:
    """GitHub #11：从 BUNDLE_DIR 的 zip 解析技能清单（lwa 无 skills/ 源码时的兜底）。"""
    preferred = BUNDLE_DIR / f"skills-{_SKILLS_VERSION}.zip"
    if preferred.is_file():
        zip_path = preferred
    else:
        candidates = sorted(BUNDLE_DIR.glob("skills-*.zip"))
        if not candidates:
            return []
        zip_path = candidates[-1]

    skills: list[SkillInfo] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for entry in sorted(zf.namelist()):
                parts = entry.strip("/").split("/")
                if len(parts) < 2 or parts[-1] != "SKILL.md":
                    continue
                name = parts[-2]
                # skills/README.md 这类顶层文件没有技能名目录，跳过
                if not name or name.startswith(".") or name == "skills":
                    continue
                info = _parse_frontmatter(zf.read(entry).decode("utf-8"), name)
                if info is not None:
                    skills.append(info)
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        _log.warning("从 bundle 解析技能清单失败（%s）: %s", zip_path, exc)
        return []
    return skills


def list_skills() -> list[SkillInfo]:
    """列出所有可用 Skills。

    优先扫描源码 SKILLS_DIR；为空时兜底解析 bundle ZIP——lwa 容器只导入
    backend/，没有 skills/ 源目录，发现面 index.json 仍需可列出全部技能（#11）。
    """
    skills: list[SkillInfo] = []
    if SKILLS_DIR.is_dir():
        for child in sorted(SKILLS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            md = child / "SKILL.md"
            if not md.is_file():
                continue
            info = _parse_skill_md(md)
            if info is not None:
                skills.append(info)
    if not skills:
        skills = _skills_from_bundle()
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


def ensure_skills_bundle() -> Path | None:
    """启动兜底：BUNDLE_DIR 无 zip 且能找到构建脚本时现场生成（lwa rebuild 后 404）。"""
    global BUNDLE_DIR
    BUNDLE_DIR = resolve_bundle_dir()
    if _bundle_has_artifact(BUNDLE_DIR):
        return BUNDLE_DIR
    script = None
    for base in (_APP_ROOT, _APP_ROOT.parent, _PROJECT_ROOT):
        candidate = base / "scripts" / "build_skills_bundle.py"
        if candidate.is_file():
            script = candidate
            break
    if script is None or not SKILLS_DIR.is_dir():
        _log.warning("Skills bundle 缺失且无法自动构建（script=%s skills=%s）", script, SKILLS_DIR)
        return None
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [sys.executable, str(script), "--output", str(BUNDLE_DIR), "--version", _SKILLS_VERSION],
            check=True,
            cwd=str(script.parent.parent),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        _log.exception("自动构建 Skills bundle 失败: %s", exc)
        return None
    return BUNDLE_DIR if _bundle_has_artifact(BUNDLE_DIR) else None


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
