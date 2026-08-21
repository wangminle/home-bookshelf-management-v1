"""WBS-5：Agent 访问控制 API（Owner 管理端）。

端点（全部要求 Owner 会话）：
- POST   /agent-access/clients            注册 Agent 客户端
- GET    /agent-access/clients            列出 Agent 客户端
- DELETE /agent-access/clients/{id}       撤销 Agent 客户端
- POST   /agent-access/grants             创建授权
- GET    /agent-access/grants             列出授权
- GET    /agent-access/grants/{id}        查看授权详情
- PATCH  /agent-access/grants/{id}        修改授权 scope
- DELETE /agent-access/grants/{id}        撤销授权
- POST   /agent-access/tokens             签发令牌
- GET    /agent-access/tokens/{grant_id}  列出令牌
- DELETE /agent-access/tokens/{id}        撤销令牌
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.agent_access import (
    AgentClientCreate,
    AgentClientOut,
    AgentGrantCreate,
    AgentGrantOut,
    AgentGrantUpdate,
    AgentTokenCreate,
    AgentTokenInfo,
    AgentTokenOut,
)
from app.services import agent_access
from app.api.v1.web_auth import require_owner

router = APIRouter(prefix="/agent-access", tags=["agent-access"])


# ── Agent Client ──

@router.post("/clients", response_model=AgentClientOut)
def create_client(
    body: AgentClientCreate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    client = agent_access.register_agent_client(
        db, display_name=body.display_name, client_type=body.client_type,
    )
    return AgentClientOut.model_validate(client)


@router.get("/clients", response_model=list[AgentClientOut])
def list_clients(
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    clients = agent_access.list_agent_clients(db)
    return [AgentClientOut.model_validate(c) for c in clients]


@router.delete("/clients/{client_id}")
def revoke_client(
    client_id: int,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    agent_access.revoke_agent_client(db, client_id)
    return {"ok": True}


# ── Agent Grant ──

@router.post("/grants", response_model=AgentGrantOut)
def create_grant(
    body: AgentGrantCreate,
    db: Session = Depends(get_db),
    owner=Depends(require_owner),
):
    grant = agent_access.create_grant(
        db,
        agent_client_id=body.agent_client_id,
        member_id=body.member_id,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
        approved_by_member_id=owner.id,
        data_scope=body.data_scope,
    )
    return _grant_to_out(grant)


@router.get("/grants", response_model=list[AgentGrantOut])
def list_grants(
    agent_client_id: int | None = None,
    member_id: int | None = None,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    grants = agent_access.list_grants(db, agent_client_id=agent_client_id, member_id=member_id)
    return [_grant_to_out(g) for g in grants]


@router.get("/grants/{grant_id}", response_model=AgentGrantOut)
def get_grant(
    grant_id: int,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    grant = agent_access.get_grant(db, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    return _grant_to_out(grant)


@router.patch("/grants/{grant_id}", response_model=AgentGrantOut)
def update_grant(
    grant_id: int,
    body: AgentGrantUpdate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    if body.scopes is not None:
        grant = agent_access.update_grant_scopes(db, grant_id, body.scopes)
    else:
        grant = agent_access.get_grant(db, grant_id)
        if grant is None:
            raise HTTPException(status_code=404, detail="授权不存在")

    if body.status == "revoked":
        agent_access.revoke_grant(db, grant_id)
        grant = agent_access.get_grant(db, grant_id)

    return _grant_to_out(grant)  # type: ignore[arg-type]


@router.delete("/grants/{grant_id}")
def revoke_grant(
    grant_id: int,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    agent_access.revoke_grant(db, grant_id)
    return {"ok": True}


# ── Agent Token ──

@router.post("/tokens", response_model=AgentTokenOut)
def issue_token(
    body: AgentTokenCreate,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    plaintext, token_row = agent_access.issue_token(db, body.grant_id)
    return AgentTokenOut(
        token=plaintext,
        token_prefix=token_row.token_prefix,
        grant_id=token_row.grant_id,
        expires_at=token_row.expires_at,
        issued_at=token_row.issued_at,
    )


@router.get("/tokens/{grant_id}", response_model=list[AgentTokenInfo])
def list_tokens(
    grant_id: int,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    tokens = agent_access.list_tokens(db, grant_id)
    return [AgentTokenInfo.model_validate(t) for t in tokens]


@router.delete("/tokens/{token_id}")
def revoke_token(
    token_id: int,
    db: Session = Depends(get_db),
    _owner=Depends(require_owner),
):
    agent_access.revoke_token(db, token_id)
    return {"ok": True}


# ── Helpers ──

def _grant_to_out(grant) -> AgentGrantOut:
    return AgentGrantOut(
        id=grant.id,
        agent_client_id=grant.agent_client_id,
        member_id=grant.member_id,
        scopes=agent_access.get_grant_scopes(grant),
        status=grant.status,
        expires_at=grant.expires_at,
        approved_by_member_id=grant.approved_by_member_id,
        approved_at=grant.approved_at,
        revoked_at=grant.revoked_at,
        data_scope=grant.data_scope_json,
        version=grant.version or 1,
        created_at=grant.created_at,
    )
