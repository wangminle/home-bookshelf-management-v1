"""MCP Agent 鉴权桥（WBS-MCP-4）。

把 MCP HTTP 请求转换为现有 Agent Principal：复用 agent_access.verify_token
（Token 摘要/到期/撤销/Client 状态逐请求校验）。

CHK-073/BUG-197 专用试点 Grant 硬门禁：真实数据试点要求——
1. Grant scopes 恰为 {books:read}（混合 Scope 不进 MCP）；
2. Grant 显式声明 data_scope == "household_shared"（历史 Grant 的
   data_scope_json 为 NULL，一律拒绝，禁止旧 Grant 祖父化进入 MCP；
   "新建专用试点 Grant" 即创建时显式携带该标记）。
Grant 级版本/Token 重签的完整绑定属权限阶段 3，当前 version 字段先落基线。
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
    grant_id: int
    grant_version: int
    member_id: int | None
    scopes: frozenset[str]
    data_scope: str | None  # 显式声明范围；历史 Grant 为 None


def build_agent_principal(db: Session, bearer: str | None) -> AgentPrincipal | None:
    """校验 Bearer Token 并构建 Principal；无效/过期/撤销返回 None。"""
    if not bearer:
        return None
    result = agent_access.verify_token(db, bearer)
    if result is None:
        return None
    _token_row, grant, client, member = result
    return AgentPrincipal(
        agent_client_id=client.id,
        agent_client_name=client.display_name,
        grant_id=grant.id,
        grant_version=agent_access.get_grant_version(grant),
        member_id=member.id,
        scopes=frozenset(agent_access.get_grant_scopes(grant)),
        data_scope=agent_access.get_grant_data_scope(grant),
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
