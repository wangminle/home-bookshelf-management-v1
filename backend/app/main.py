from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
# 一期默认允许局域网前端；生产可按需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)


# ---- 前端静态文件托管（SPA fallback）----
# 当 backend/static/ 目录存在时（生产部署），由后端直接托管前端构建产物。
# /api/v1 路由已在上方注册，优先于 StaticFiles 匹配。
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    # 挂载 /assets 静态资源（JS/CSS 带 hash 文件名，可长期缓存）
    _assets_dir = _STATIC_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    # SPA fallback：所有未匹配的非 /api 请求返回 index.html
    _index_html = _STATIC_DIR / "index.html"
    _static_root = _STATIC_DIR.resolve()

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        """非 /api 路径返回前端 SPA，让 vue-router 接管路由。"""
        # 未匹配的 /api/* 必须 404 JSON，不可回落成 index.html（避免前端当成功 HTML 解析）
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # 修复 BUG-106：路径穿越防护——解析后必须仍在 _STATIC_DIR 内，
        # 阻止 /../config/.env.example 等读取 backend 旁路文件
        candidate = (_STATIC_DIR / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_static_root):
            return FileResponse(str(candidate))
        # 其余路径回退到 index.html（SPA 路由）
        return FileResponse(str(_index_html))
else:
    @app.get("/")
    def root() -> dict[str, str]:
        return {"message": settings.app_name, "docs": "/docs"}
