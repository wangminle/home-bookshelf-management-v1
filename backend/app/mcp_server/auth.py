"""MCP Agent 鉴权桥（WBS-MCP-4）。

把 MCP HTTP 请求转换为现有 Agent Principal：复用 agent_access.verify_token
（Token 摘要/到期/撤销/Client 状态逐请求校验）。

CHK-073/BUG-197 专用试点 Grant 硬门禁：真实数据试点要求——
1. Grant scopes 恰为 {books:read}（混合 Scope 不进 MCP）；
2. Grant 显式声明 data_scope == "household_shared"（历史 Grant 的
   data_scope_json 为 NULL，一律拒绝，禁止旧 Grant 祖父化进入 MCP；
   "新建专用试点 Grant" 即创建时显式携带该标记）。
BUG-213：令牌绑定签发时的授权版本已落地--AgentToken.grant_version 快照
+ verify_token 版本一致性校验 + 范围变更时版本递增并吊销旧令牌（无重启/
缓存依赖，WBS-MCP-4.4 立即收窄口径）。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services import agent_access

MCP_REQUIRED_SCOPE = "books:read"
PILOT_DATA_SCOPE = "household_shared"


@dataclass(frozen=True)
class AgentPrincipal:
    agent_client_id: int
    agent_client_name: str
    agent_client_type: str | None  # 客户端类型（如 claude-code/自定义），仅审计用
    grant_id: int
    grant_version: int
    member_id: int | None
    scopes: frozenset[str]
    data_scope: str | None  # 显式声明范围；历史 Grant 为 None
    token_prefix: str | None  # 令牌前缀（非明文，审计可关联令牌记录）


def build_agent_principal(db: Session, bearer: str | None) -> AgentPrincipal | None:
    """校验 Bearer Token 并构建 Principal；无效/过期/撤销返回 None。

    有效权限 = 凭证 Scope ∩ 服务端可授予上限（AGENT_GRANTABLE_SCOPES）——
    即便历史 Grant 中混入未来才定义的能力名，也不会在 MCP 生效（MCP 第二期
    清单第 2 点：不因凭证自称而放大）。
    """
    if not bearer:
        return None
    result = agent_access.verify_token(db, bearer)
    if result is None:
        return None
    _token_row, grant, client, member = result
    from app.services.permission_policy import AGENT_GRANTABLE_SCOPES

    granted = frozenset(agent_access.get_grant_scopes(grant))
    return AgentPrincipal(
        agent_client_id=client.id,
        agent_client_name=client.display_name,
        agent_client_type=getattr(client, "client_type", None),
        grant_id=grant.id,
        grant_version=agent_access.get_grant_version(grant),
        member_id=member.id,
        scopes=frozenset(granted & AGENT_GRANTABLE_SCOPES),
        data_scope=agent_access.get_grant_data_scope(grant),
        token_prefix=_token_row.token_prefix,
    )


def require_mcp_scope(principal: AgentPrincipal, scope: str = MCP_REQUIRED_SCOPE) -> None:
    if scope not in principal.scopes:
        raise HTTPException(
            status_code=403,
            detail="SCOPE_DENIED",
            headers={"X-Error-Code": "SCOPE_DENIED"},
        )


def require_pilot_grant(principal: AgentPrincipal) -> None:
    """专用试点 Grant 硬门禁（CHK-073/BUG-197）。"""
    if set(principal.scopes) != {MCP_REQUIRED_SCOPE} or principal.data_scope != PILOT_DATA_SCOPE:
        raise HTTPException(
            status_code=403,
            detail="PILOT_GRANT_REQUIRED",
            headers={"X-Error-Code": "PILOT_GRANT_REQUIRED"},
        )
