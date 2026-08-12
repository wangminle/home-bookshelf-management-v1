"""WBS-6：auth.py 向后兼容入口。

原有功能已迁移到 auth_context.py（统一 AuthContext）。
此文件保留导入兼容，确保现有 API 端点无需大规模改动。

新代码应直接 from app.auth_context import AuthContext, require_auth, require_scope。
"""
from __future__ import annotations

# 从 auth_context 重新导出所有公共接口
from app.auth_context import (  # noqa: F401
    AuthContext,
    ChannelIdentity,
    authorize_member_bind,
    channel_headers,
    enforce_channel_member,
    expected_channel_signature,
    find_members_by_binding,
    member_count,
    resolve_member_by_binding,
    system_has_channel_bindings,
    require_complete_channel_headers,
    build_auth_context,
    require_auth,
    require_scope,
    verify_csrf,
)
