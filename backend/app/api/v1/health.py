from fastapi import APIRouter, Depends, Response, status

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth_context import AuthContext, require_scope, system_has_channel_bindings
from app.config import settings
from app import db as db_module
from app.schemas.agent_discovery import PublicHealthData
from app.schemas.book import ApiResponse, HealthOut
from app.services.agent_discovery import build_public_health

router = APIRouter()


def _barcode_scan_available() -> bool:
    try:
        from PIL import Image  # noqa: F401
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError:
        return False
    return True


@router.get("/public-health", response_model=ApiResponse)
def public_health() -> ApiResponse:
    """WBS-2：公共健康检查。只返回最小可用性，不暴露数据库状态、第三方 Key 配置等内部细节。"""
    return ApiResponse(ok=True, data=build_public_health(), error=None)


@router.get("/health", response_model=ApiResponse)
def health_check(
    response: Response,
    _ctx: AuthContext = Depends(require_scope("members:read")),
) -> ApiResponse:
    """WBS-2：受保护诊断端点（BUG-167 落地矩阵声明的 members:read）。
    暴露数据库连接、Google Books Key 配置和条码依赖状态；
    无凭证探活请用 /public-health（Docker healthcheck 已切换）。

    权限阶段 0（任务 0.7）：追加部署信任态势（基线 §11.3）——渠道签名、
    渠道绑定、可信代理与 PUBLIC_BASE_URL 的配置事实，供 doctor 检查
    “LAN 暴露但无可信 CIDR”“反代但无 HTTPS”“渠道启用但无签名”等不一致。
    """
    database = "connected"
    channel_bindings_present = False
    try:
        with db_module.SessionLocal() as db:
            db.execute(text("SELECT 1"))
            channel_bindings_present = system_has_channel_bindings(db)
    except SQLAlchemyError:
        database = "disconnected"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    ok = database == "connected"
    return ApiResponse(
        ok=ok,
        data=HealthOut(
            status="ok" if ok else "degraded",
            app=settings.app_name,
            database=database,
            google_books_configured=bool(settings.google_books_api_key),
            barcode_scan_available=_barcode_scan_available(),
            channel_signing_configured=bool(settings.channel_signing_secret),
            channel_bindings_present=channel_bindings_present,
            # CHK-071：按成功解析的网络数判断——无效 CIDR 被静默跳过时
            # 不能继续报告"已配置"，否则 doctor 不告警但 LAN 请求全被拒
            trusted_proxies_configured=len(settings.trusted_proxy_networks) > 0,
            public_base_url=settings.public_base_url,
            public_url_https=bool(
                settings.public_base_url and settings.public_base_url.startswith("https://")
            ),
            anonymous_catalog_mode=settings.anonymous_catalog_mode,
            trusted_lan_configured=len(settings.trusted_lan_networks) > 0,
        ),
        error=None if ok else "database disconnected",
    )
