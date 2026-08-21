"""权限阶段 0：服务器端权限策略常量与角色能力集。

唯一基线：design/权限-数据分层与用户角色设计建议-20260820.md
- §5.3 角色 → 服务器内置能力集（角色与 Scope 共用同一套能力命名）；
- §5.4 渠道有效权限 = action IN 绑定 Member 的 Role 能力集；
- §6.4 高风险 Scope 与 Agent 可授予集合；旧 Scope → 新动作名兼容映射；
- §14 阶段 0：先冻结契约与能力表，不在本阶段启用 Scope 重命名。

本模块只维护常量与纯函数，不触碰数据库与请求上下文。
"""
from __future__ import annotations

# ── 全量 Scope（现名） ──

ALL_SCOPES: frozenset[str] = frozenset({
    "books:read", "books:write", "books:delete",
    "reading:read", "reading:write",
    "notes:read", "notes:write",
    "purchases:read", "purchases:write",
    "stats:read", "stats:household",
    "files:read", "members:read",
})

# ── 角色能力集（基线 §5.1 角色能力表 → 现有 Scope 命名） ──

# Owner：家庭系统管理员，人工管理 + Agent 授权批准者，全量能力。
OWNER_ROLE_SCOPES: frozenset[str] = ALL_SCOPES

# Member：独立登录家庭成员。
# 不含 books:delete（删除书目主记录仅 Owner，需风险确认）；
# 不含 stats:household（全家庭统计仅 Owner；Member 默认仅本人统计 stats:read）。
MEMBER_ROLE_SCOPES: frozenset[str] = ALL_SCOPES - {"books:delete", "stats:household"}


def role_scopes(role: str | None) -> frozenset[str]:
    """按角色返回服务器内置能力集。未知角色按最小能力集（member）处理。"""
    if role == "owner":
        return OWNER_ROLE_SCOPES
    return MEMBER_ROLE_SCOPES


# ── Agent 可授予集合与风险分级（基线 §6.4/§6.5） ──

# 可进入 Agent Grant 的 Scope。管理类能力（members:manage、agent_grants:manage、
# security:configure、audit:full 等）永远不进入本集合——管理 API 始终要求
# Owner Web 会话；当前系统尚无这些 Scope 名称，validate_scopes 天然拒绝。
AGENT_GRANTABLE_SCOPES: frozenset[str] = ALL_SCOPES

# 高风险 Scope：破坏性删除 / 跨成员家庭聚合。Grant 含这些 Scope 时：
# 只能由 Owner 批准（create_grant 强制校验批准者角色），建议较短有效期
# （§7.5 风险档：7 天，完整约束落地属权限阶段 3）。
HIGH_RISK_SCOPES: frozenset[str] = frozenset({"books:delete", "stats:household"})

# ── 旧 Scope → 新动作名兼容映射（基线 §6.4 / 阶段 0 任务 4） ──
#
# 仅作为集中、版本化的迁移对照表，本阶段不启用任何运行时重命名：
# - 未来统一迁移到新名称时，映射必须在此集中配置并测试（MCP 设计 §8.4）；
# - copies:read/copies:write/catalog:batch_update 将从 books:read/books:write
#   拆分，拆分动作单独评审，不预先落入本表。

SCOPE_COMPAT_MAP: dict[str, str] = {
    "books:read": "catalog:read",
    "books:write": "catalog:write",
    "books:delete": "catalog:delete",
    "reading:read": "reading:read",
    "reading:write": "reading:write",
    "notes:read": "notes:read",
    "notes:write": "notes:write",
    "purchases:read": "purchases:read",
    "purchases:write": "purchases:write",
    "stats:read": "stats:self",
    "stats:household": "stats:aggregate",
    "members:read": "members:read_basic",
    "files:read": "files:read",
}
