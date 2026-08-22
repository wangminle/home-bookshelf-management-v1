"""权限阶段 0（任务 0.4/0.5/0.6）：权限策略模块单元测试。

覆盖权限基线（design/plans/权限-数据分层与用户角色设计建议-20260820.md）：
- §5.3 角色 → 服务器内置能力集；
- §6.4 高风险 Scope 与 Agent 可授予集合；
- §14 阶段 0 任务 4：旧 Scope → 新动作名兼容映射（集中配置、不启用重命名）。
"""
from __future__ import annotations

from app.services import permission_policy


def test_owner_role_scopes_are_full() -> None:
    """Owner 角色能力集 = 全量 Scope（Web owner 短路之外的显式定义）。"""
    assert permission_policy.OWNER_ROLE_SCOPES == permission_policy.ALL_SCOPES


def test_member_role_scopes_exclude_owner_only_capabilities() -> None:
    """Member 角色能力集不含 owner 专属能力（基线 §5.1）。

    - books:delete：删除书目主记录仅 Owner；
    - stats:household：全家庭统计仅 Owner（Member 默认仅本人统计 stats:read）。
    """
    assert permission_policy.MEMBER_ROLE_SCOPES < permission_policy.ALL_SCOPES
    assert "books:delete" not in permission_policy.MEMBER_ROLE_SCOPES
    assert "stats:household" not in permission_policy.MEMBER_ROLE_SCOPES
    # Member 日常能力保留：书目维护、本人阅读/笔记/购买、本人统计、文件与成员名单
    for scope in ("books:read", "books:write", "reading:read", "reading:write",
                  "notes:read", "notes:write", "purchases:read", "purchases:write",
                  "stats:read", "files:read", "members:read"):
        assert scope in permission_policy.MEMBER_ROLE_SCOPES


def test_role_scopes_known_roles() -> None:
    assert permission_policy.role_scopes("owner") == permission_policy.OWNER_ROLE_SCOPES
    assert permission_policy.role_scopes("member") == permission_policy.MEMBER_ROLE_SCOPES


def test_role_scopes_unknown_role_fails_closed() -> None:
    """BUG-190：未知/历史脏角色（guest 等）一律空集 fail-closed，不回退 member。"""
    assert permission_policy.role_scopes("guest") == frozenset()
    assert permission_policy.role_scopes("") == frozenset()
    assert permission_policy.role_scopes(None) == frozenset()  # type: ignore[arg-type]


def test_agent_grantable_scopes_subset_of_all() -> None:
    """Agent 可授予集合必须是全量 Scope 的子集；管理能力永不进入普通 Agent Scope。"""
    assert permission_policy.AGENT_GRANTABLE_SCOPES <= permission_policy.ALL_SCOPES
    # 基线 §6.4：管理类能力不进入 Agent Scope（当前尚无这些 Scope，防御性约束）
    for scope in ("members:manage", "roles:manage", "auth:manage",
                  "agent_grants:approve", "agent_grants:manage",
                  "security:configure", "audit:full", "backup:manage"):
        assert scope not in permission_policy.AGENT_GRANTABLE_SCOPES


def test_high_risk_scopes_definition() -> None:
    """高风险 Scope 分级：破坏性删除与跨成员家庭聚合（基线 §6.4/§11.2）。"""
    assert permission_policy.HIGH_RISK_SCOPES == {"books:delete", "stats:household"}
    assert permission_policy.HIGH_RISK_SCOPES <= permission_policy.ALL_SCOPES


def test_scope_compat_map_is_total_over_all_scopes() -> None:
    """兼容映射必须覆盖当前全部 Scope（迁移时无遗漏），值唯一（无二义）。"""
    assert set(permission_policy.SCOPE_COMPAT_MAP) == set(permission_policy.ALL_SCOPES)
    values = list(permission_policy.SCOPE_COMPAT_MAP.values())
    assert len(values) == len(set(values)), "映射目标不得重复"


def test_scope_compat_map_expected_targets() -> None:
    """基线 §6.4 的目标命名：catalog:*/stats:self/stats:aggregate/members:read_basic。"""
    m = permission_policy.SCOPE_COMPAT_MAP
    assert m["books:read"] == "catalog:read"
    assert m["books:write"] == "catalog:write"
    assert m["books:delete"] == "catalog:delete"
    assert m["stats:read"] == "stats:self"
    assert m["stats:household"] == "stats:aggregate"
    assert m["members:read"] == "members:read_basic"
    # 名称不变的 Scope 保持恒等映射
    assert m["reading:read"] == "reading:read"
    assert m["files:read"] == "files:read"


def test_agent_access_reexports_all_scopes() -> None:
    """agent_access.ALL_SCOPES 与策略模块同源（历史引用不漂移）。"""
    from app.services import agent_access
    assert agent_access.ALL_SCOPES == permission_policy.ALL_SCOPES
