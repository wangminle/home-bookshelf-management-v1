"""WBS-5：Agent 访问控制服务层。

职责：
- Agent Client 注册与撤销
- Grant 创建、撤销、范围管理
- Token 生成（hbs_at_<public_id>_<secret>）、SHA-256 存储、验证
- Owner 密码管理（Argon2id）
- Web 会话创建与验证
"""
from __future__ import annotations

import hashlib
import json
import secrets
import string
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentClient, AgentGrant, AgentToken, Member, MemberCredential, WebSession
from app.services.permission_policy import (
    AGENT_GRANTABLE_SCOPES,
    ALL_SCOPES,  # noqa: F401 — 历史引用经 agent_access.ALL_SCOPES 继续可用
)

# ── 常量 ──

TOKEN_PREFIX = "hbs_at_"
TOKEN_SECRET_LEN = 32  # base62 字符
SESSION_TTL_HOURS = 24
MAX_LOGIN_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15

_argon2 = PasswordHasher(
    time_cost=settings.argon2_time_cost,
    memory_cost=settings.argon2_memory_cost,
    parallelism=settings.argon2_parallelism,
)

_BASE62_ALPHABET = string.ascii_letters + string.digits


def _gen_secret(length: int = TOKEN_SECRET_LEN) -> str:
    return "".join(secrets.choice(_BASE62_ALPHABET) for _ in range(length))


def _gen_public_id() -> str:
    return secrets.token_hex(8)


def _gen_session_token() -> str:
    return secrets.token_urlsafe(48)


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _now() -> datetime:
    """Naive UTC — SQLite 不保留 tzinfo，比较时需要一致。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Scope 验证 ──

def validate_scopes(scopes: list[str]) -> list[str]:
    """验证 scope 列表，返回去重后的有效列表。

    权限阶段 0（任务 0.6）：只接受 AGENT_GRANTABLE_SCOPES 内的能力名——
    管理类能力（members:manage、agent_grants:manage 等）永不进入 Agent Grant。
    """
    invalid = [s for s in scopes if s not in AGENT_GRANTABLE_SCOPES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"未知的 scope: {', '.join(invalid)}。有效 scope: {sorted(AGENT_GRANTABLE_SCOPES)}",
        )
    # 去重保序
    seen: set[str] = set()
    result: list[str] = []
    for s in scopes:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


# ── Agent Client ──

def register_agent_client(
    db: Session,
    *,
    display_name: str,
    client_type: str | None = None,
) -> AgentClient:
    client = AgentClient(
        public_id=_gen_public_id(),
        display_name=display_name,
        client_type=client_type,
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return client


def get_agent_client(db: Session, client_id: int) -> AgentClient | None:
    return db.get(AgentClient, client_id)


def list_agent_clients(db: Session) -> list[AgentClient]:
    return list(db.scalars(select(AgentClient).order_by(AgentClient.created_at.desc())))


def revoke_agent_client(db: Session, client_id: int) -> None:
    client = get_agent_client(db, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Agent 客户端不存在")
    client.revoked_at = _now()
    # 同时撤销所有活跃 grant
    for grant in client.grants:
        if grant.status == "active":
            grant.status = "revoked"
            grant.revoked_at = _now()
            for token in grant.tokens:
                if token.revoked_at is None:
                    token.revoked_at = _now()
    db.commit()


# ── Agent Grant ──

# 试点期允许显式声明的数据范围（基线 §6.3 的最小落地；其余属阶段 3）
PILOT_DATA_SCOPES = frozenset({"household_shared"})


def create_grant(
    db: Session,
    *,
    agent_client_id: int,
    member_id: int,
    scopes: list[str],
    expires_in_days: int = 30,
    approved_by_member_id: int | None = None,
    data_scope: str | None = None,
) -> AgentGrant:
    client = get_agent_client(db, agent_client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Agent 客户端不存在")
    if client.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Agent 客户端已撤销")

    member = db.get(Member, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="家庭成员不存在")

    scopes_validated = validate_scopes(scopes)

    # CHK-073/BUG-197：数据范围必须显式声明且在试点允许集内；
    # 不传 = 历史语义（无数据范围标记，MCP 等真实数据门控拒绝）
    if data_scope is not None and data_scope not in PILOT_DATA_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"未知 data_scope: {data_scope}。当前允许: {sorted(PILOT_DATA_SCOPES)}",
        )

    # 权限阶段 0（任务 0.6）：Grant 只能由 Owner 批准（基线 §6.2/§7.1）。
    # 服务层强制校验，不再默认"绑定成员自批"；非 Owner 主体（渠道/未来 Member
    # Web 会话）最多提交申请，批准入口永远在 Owner。
    if approved_by_member_id is None:
        raise HTTPException(
            status_code=403,
            detail="Agent Grant 必须显式指定批准者，且批准者必须是 owner",
        )
    approver = db.get(Member, approved_by_member_id)
    if approver is None or approver.role != "owner":
        raise HTTPException(
            status_code=403,
            detail="Agent Grant 只能由 owner 批准",
        )
    approver_id = approved_by_member_id

    grant = AgentGrant(
        agent_client_id=agent_client_id,
        member_id=member_id,
        scopes_json=json.dumps(scopes_validated),
        status="active",
        expires_at=_now() + timedelta(days=expires_in_days),
        approved_by_member_id=approver_id,
        data_scope_json=data_scope,
        version=1,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


def list_grants(db: Session, *, agent_client_id: int | None = None, member_id: int | None = None) -> list[AgentGrant]:
    stmt = select(AgentGrant).order_by(AgentGrant.created_at.desc())
    if agent_client_id is not None:
        stmt = stmt.where(AgentGrant.agent_client_id == agent_client_id)
    if member_id is not None:
        stmt = stmt.where(AgentGrant.member_id == member_id)
    return list(db.scalars(stmt))


def get_grant(db: Session, grant_id: int) -> AgentGrant | None:
    return db.get(AgentGrant, grant_id)


def get_grant_data_scope(grant: AgentGrant) -> str | None:
    """显式数据范围；历史 Grant 返回 None（调用方按未声明处理）。"""
    return grant.data_scope_json


def get_grant_version(grant: AgentGrant) -> int:
    return grant.version or 1


def get_grant_scopes(grant: AgentGrant) -> list[str]:
    try:
        return json.loads(grant.scopes_json)
    except (json.JSONDecodeError, TypeError):
        return []


def revoke_grant(db: Session, grant_id: int) -> None:
    grant = get_grant(db, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    grant.status = "revoked"
    grant.revoked_at = _now()
    for token in grant.tokens:
        if token.revoked_at is None:
            token.revoked_at = _now()
    db.commit()


def update_grant_scopes(db: Session, grant_id: int, scopes: list[str]) -> AgentGrant:
    """更新授权范围（BUG-213：立即收窄口径）。

    范围集合实际变更时：grant.version 递增，并吊销该授权下所有未吊销令牌
    （旧令牌绑定旧版本，即使侥幸存活也会被 verify_token 的版本一致性
    校验拒绝）。范围未变时不做版本递增/吊销（避免无意义地打断正常使用）。
    无论是否变更，新签发令牌都绑定递增后的当前版本。
    """
    grant = get_grant(db, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    if grant.status != "active":
        raise HTTPException(status_code=400, detail="只能修改活跃状态的授权")
    scopes_validated = validate_scopes(scopes)

    old_scopes = get_grant_scopes(grant)
    changed = set(old_scopes) != set(scopes_validated)
    if changed:
        grant.version = get_grant_version(grant) + 1
        now = _now()
        for token_row in grant.tokens:
            if token_row.revoked_at is None:
                token_row.revoked_at = now
    grant.scopes_json = json.dumps(scopes_validated)
    db.commit()
    db.refresh(grant)
    return grant


# ── Agent Token ──

def issue_token(db: Session, grant_id: int) -> tuple[str, AgentToken]:
    """生成 token 明文 + DB 记录。明文只返回一次。"""
    grant = get_grant(db, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="授权不存在")
    if grant.status != "active":
        raise HTTPException(status_code=400, detail="授权非活跃状态，无法签发令牌")

    client = grant.agent_client
    if client is None or client.revoked_at is not None:
        raise HTTPException(status_code=400, detail="Agent 客户端已撤销")

    now = _now()
    if grant.expires_at <= now:
        raise HTTPException(status_code=400, detail="授权已过期")

    secret = _gen_secret()
    plaintext = f"{TOKEN_PREFIX}{client.public_id}_{secret}"
    token_row = AgentToken(
        grant_id=grant_id,
        token_prefix=plaintext[: len(TOKEN_PREFIX) + 8],  # hbs_at_XXXXXXXX
        token_hash=_hash_token(plaintext),
        issued_at=now,
        expires_at=grant.expires_at,
        # BUG-213：绑定签发时的授权版本（范围变更后旧令牌全部失效）
        grant_version=get_grant_version(grant),
    )
    db.add(token_row)
    # 更新 client last_seen
    client.last_seen_at = now
    db.commit()
    db.refresh(token_row)
    return plaintext, token_row


def verify_token(db: Session, plaintext: str) -> tuple[AgentToken, AgentGrant, AgentClient, Member] | None:
    """验证 token，返回 (token, grant, client, member) 或 None。

    副作用：更新 last_used_at；若 grant 过期则标记。
    """
    if not plaintext.startswith(TOKEN_PREFIX):
        return None
    token_hash = _hash_token(plaintext)
    token_row = db.scalar(select(AgentToken).where(AgentToken.token_hash == token_hash))
    if token_row is None:
        return None
    if token_row.revoked_at is not None:
        return None

    now = _now()
    if token_row.expires_at <= now:
        return None

    grant = token_row.grant
    if grant is None or grant.status != "active":
        return None
    # BUG-213：令牌绑定的授权版本必须与当前一致。范围变更（版本递增）后
    # 旧令牌即使未被吊销也立即失效，防止旧范围令牌继承新范围（或反向复活）
    if (token_row.grant_version or 1) != get_grant_version(grant):
        return None
    if grant.expires_at <= now:
        grant.status = "expired"
        db.commit()
        return None

    client = grant.agent_client
    if client is None or client.revoked_at is not None:
        return None

    member = db.get(Member, grant.member_id)
    if member is None:
        return None
    # BUG-203：停用成员的 Agent 凭据即时失效（与 Web 会话/渠道口径一致）
    if member.disabled_at is not None:
        return None

    # 更新使用时间
    token_row.last_used_at = now
    client.last_seen_at = now
    db.commit()

    return token_row, grant, client, member


def list_tokens(db: Session, grant_id: int) -> list[AgentToken]:
    return list(db.scalars(
        select(AgentToken).where(AgentToken.grant_id == grant_id).order_by(AgentToken.issued_at.desc())
    ))


def revoke_token(db: Session, token_id: int) -> None:
    token_row = db.get(AgentToken, token_id)
    if token_row is None:
        raise HTTPException(status_code=404, detail="令牌不存在")
    token_row.revoked_at = _now()
    db.commit()


# ── 成员凭据（权限阶段 2：Owner/Member 统一，基线 §12.1） ──

def get_owner_member(db: Session) -> Member | None:
    """获取 owner 角色的成员。"""
    return db.scalar(select(Member).where(Member.role == "owner"))


def get_member_credential(db: Session, member_id: int) -> MemberCredential | None:
    return db.scalar(select(MemberCredential).where(MemberCredential.member_id == member_id))


def member_has_password(db: Session, member_id: int) -> bool:
    return get_member_credential(db, member_id) is not None


def has_owner_password(db: Session) -> bool:
    """兼容既有调用（/auth/status、init-password 守卫、admin CLI）：owner 是否已设密码。"""
    owner = get_owner_member(db)
    if owner is None:
        return False
    return member_has_password(db, owner.id)


def set_member_password(db: Session, member: Member, password: str) -> None:
    """设置/重置成员密码（重置后由调用方决定撤销会话）。

    任何凭据都隐含登录身份：无用户名的成员在此补齐（按显示名生成唯一值）。
    """
    hashed = _argon2.hash(password)
    cred = get_member_credential(db, member.id)
    if cred is None:
        cred = MemberCredential(member_id=member.id, password_hash=hashed)
        db.add(cred)
    else:
        cred.password_hash = hashed
        cred.failed_attempts = 0
        cred.locked_until = None
    if not member.username:
        member.username = ensure_unique_username(db, member.name or "user", exclude_id=member.id)
    db.commit()


def set_owner_password(db: Session, password: str) -> None:
    """兼容既有调用（init-password/admin CLI）：设置 owner 密码并补登录用户名。"""
    owner = get_owner_member(db)
    if owner is None:
        raise HTTPException(status_code=400, detail="系统中尚无 owner 成员，请先完成初始化")
    set_member_password(db, owner, password)  # 用户名兜底由 set_member_password 统一处理


def ensure_unique_username(db: Session, base: str, *, exclude_id: int | None = None) -> str:
    """以显示名（或指定基名）生成唯一用户名，冲突追加 _2/_3…。

    BUG-203：exclude_id 生效（改名场景排除自身现有用户名）；
    BUG-204：与 lower(username) 唯一索引同口径，按小写比较冲突。
    """
    base = (base or "user").strip() or "user"
    from sqlalchemy import func

    stmt = select(func.lower(Member.username)).where(Member.username.is_not(None))
    if exclude_id is not None:
        stmt = stmt.where(Member.id != exclude_id)
    candidates = {(row[0] or "") for row in db.execute(stmt).all()}
    username, seq = base, 2
    while username.lower() in candidates:
        username, seq = f"{base}_{seq}", seq + 1
    return username


def resolve_login_member(db: Session, username: str | None) -> Member | None:
    """按用户名解析登录成员（大小写不敏感）。

    未提供用户名时：系统恰有一条凭据则回退到该成员（单账号家庭/兼容
    既有仅密码登录流程），多于一条则返回 None（调用方要求提供用户名）。
    """
    if username:
        # BUG-204：lower(username) 唯一索引保证至多一行，单次 CI 查询无歧义
        from sqlalchemy import func

        return db.scalar(
            select(Member).where(func.lower(Member.username) == username.strip().lower())
        )
    rows = db.scalars(select(MemberCredential.member_id)).all()
    if len(rows) == 1:
        return db.get(Member, rows[0])
    return None


def verify_member_password(db: Session, member: Member, password: str) -> bool:
    """验证成员密码（含停用检查与防爆破锁定）。成功 True；失败/锁定抛错或 False。"""
    if member.disabled_at is not None:
        raise HTTPException(status_code=403, detail="该成员已停用，无法登录")
    cred = get_member_credential(db, member.id)
    if cred is None:
        return False

    now = _now()
    if cred.locked_until is not None and cred.locked_until > now:
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过多，已锁定至 {cred.locked_until.strftime('%H:%M')} UTC",
        )

    try:
        _argon2.verify(cred.password_hash, password)
    except VerifyMismatchError:
        cred.failed_attempts += 1
        if cred.failed_attempts >= MAX_LOGIN_ATTEMPTS:
            cred.locked_until = now + timedelta(minutes=LOCK_DURATION_MINUTES)
            cred.failed_attempts = 0
        db.commit()
        return False

    cred.failed_attempts = 0
    cred.locked_until = None
    db.commit()
    return True


def verify_owner_password(db: Session, password: str) -> Member | None:
    """兼容既有调用：仅密码登录 owner（无用户名，要求 owner 恰为单凭据场景）。"""
    owner = get_owner_member(db)
    if owner is None:
        return None
    if verify_member_password(db, owner, password):
        return owner
    return None


def change_member_password(
    db: Session, member: Member, *, old_password: str, new_password: str
) -> bool:
    """自助修改密码：验证旧密码成功后设置新密码。返回是否成功。"""
    if not verify_member_password(db, member, old_password):
        return False
    set_member_password(db, member, new_password)
    return True


def revoke_member_sessions(
    db: Session, member_id: int, *, keep_token: str | None = None
) -> int:
    """撤销成员全部会话（角色变化/密码重置/停用后调用）。返回撤销数。"""
    now = _now()
    stmt = (
        update(WebSession)
        .where(
            WebSession.member_id == member_id,
            WebSession.revoked_at.is_(None),
            WebSession.expires_at > now,
        )
        .values(revoked_at=now)
    )
    if keep_token is not None:
        stmt = stmt.where(WebSession.session_token != keep_token)
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


# ── Web Session ──

def create_web_session(
    db: Session,
    member_id: int,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, WebSession]:
    """创建 Web 会话，返回 (session_token, session_row)。"""
    token = _gen_session_token()
    now = _now()
    session = WebSession(
        session_token=token,
        member_id=member_id,
        created_at=now,
        expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return token, session


def verify_web_session(db: Session, session_token: str) -> Member | None:
    if not session_token:
        return None
    session = db.scalar(select(WebSession).where(WebSession.session_token == session_token))
    if session is None:
        return None
    if session.revoked_at is not None:
        return None
    if session.expires_at <= _now():
        return None
    member = db.get(Member, session.member_id)
    if member is None:
        return None
    # 权限阶段 2：停用成员的既有会话立即失效
    if member.disabled_at is not None:
        return None
    return member


def revoke_web_session(db: Session, session_token: str) -> None:
    session = db.scalar(select(WebSession).where(WebSession.session_token == session_token))
    if session is not None:
        session.revoked_at = _now()
        db.commit()


def cleanup_expired_sessions(db: Session) -> int:
    """清理过期会话，返回清理数量。"""
    now = _now()
    result = db.execute(
        update(WebSession)
        .where(WebSession.expires_at <= now, WebSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.commit()
    return result.rowcount  # type: ignore[return-value]
