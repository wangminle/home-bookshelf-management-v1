from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi import Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Member
from app.utils.member_helpers import resolve_member_id


@dataclass
class ChannelIdentity:
    member_id: int | None
    channel: str | None
    external_user_id: str | None
    setup_token: str | None = None


def _normalize_header(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def require_complete_channel_headers(channel: str | None, external_user_id: str | None) -> None:
    """渠道头必须成对出现：只传其中一个视为畸形请求。"""
    has_channel = bool(channel)
    has_external = bool(external_user_id)
    if has_channel ^ has_external:
        raise HTTPException(
            status_code=400,
            detail="X-Channel 与 X-External-User-Id 必须同时提供或同时省略",
        )


def member_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Member)) or 0


def system_has_channel_bindings(db: Session) -> bool:
    """系统内是否已有任意渠道绑定（用于区分初始化与后续加固）。"""
    members = db.scalars(select(Member)).all()
    for member in members:
        if not member.channel_bindings:
            continue
        try:
            parsed = json.loads(member.channel_bindings)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and any(
            v is not None and str(v).strip() for v in parsed.values()
        ):
            return True
    return False


def find_members_by_binding(db: Session, channel: str, external_user_id: str) -> list[Member]:
    """返回所有绑定了指定渠道身份的成员（用于唯一性检查）。"""
    matched: list[Member] = []
    members = db.scalars(select(Member)).all()
    for member in members:
        if not member.channel_bindings:
            continue
        try:
            parsed = json.loads(member.channel_bindings)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        bound = parsed.get(channel)
        if bound is not None and str(bound) == str(external_user_id):
            matched.append(member)
    return matched


def resolve_member_by_binding(db: Session, channel: str, external_user_id: str) -> Member | None:
    """根据 channel + external_user_id 反查绑定的成员。

    若同一身份绑定到多个成员（历史脏数据），按 id 升序取第一个并保持确定性。
    """
    matched = find_members_by_binding(db, channel, external_user_id)
    if not matched:
        return None
    return sorted(matched, key=lambda m: m.id)[0]


def enforce_channel_member(
    db: Session,
    *,
    body_member_id: int | None,
    channel: str | None,
    external_user_id: str | None,
    require_channel: bool = False,
) -> int:
    """渠道身份鉴权：

    - 渠道头必须成对；只传一个 → 400。
    - 无渠道头：
      - require_channel=True -> 403，拒绝匿名。
      - require_channel=False -> 回退到 resolve_member_id（一期可信局域网兜底）。
    - 有渠道头但未绑定：返回 403，拒绝冒用。
    - 有渠道头且已绑定：以绑定成员为准；若 body 同时指定了不同的 member_id，则 403。
    """
    channel = _normalize_header(channel)
    external_user_id = _normalize_header(external_user_id)
    require_complete_channel_headers(channel, external_user_id)

    if not channel and not external_user_id:
        if require_channel:
            raise HTTPException(
                status_code=403,
                detail="此端点要求渠道身份鉴权，请提供 X-Channel 与 X-External-User-Id",
            )
        try:
            return resolve_member_id(db, body_member_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    member = resolve_member_by_binding(db, channel, external_user_id)  # type: ignore[arg-type]
    if member is None:
        raise HTTPException(
            status_code=403,
            detail=f"渠道 {channel} 的外部用户 {external_user_id} 未绑定任何家庭成员",
        )
    if body_member_id is not None and body_member_id != member.id:
        raise HTTPException(
            status_code=403,
            detail=f"渠道身份与指定 member_id 不一致（渠道绑定 {member.id}，请求 {body_member_id}）",
        )
    return member.id


def authorize_member_bind(
    db: Session,
    *,
    target_member_id: int,
    channel: str | None,
    external_user_id: str | None,
    setup_token: str | None,
) -> None:
    """保护 POST /members/bind，防止匿名自助写入白名单。

    允许的情形：
    1. 空库引导（尚无成员）——与 BUG-035 兼容；
    2. 系统尚无任何渠道绑定（允许 README：先创建成员再完成首次初始化绑定）；
    3. 提供了正确的 X-Setup-Token（与 settings.setup_token 一致）；
    4. 提供了已绑定渠道头，且调用者为 owner，或正在为自己绑定/改绑。
    """
    channel = _normalize_header(channel)
    external_user_id = _normalize_header(external_user_id)
    setup_token = _normalize_header(setup_token)
    require_complete_channel_headers(channel, external_user_id)

    expected = _normalize_header(settings.setup_token)
    if expected and setup_token and setup_token == expected:
        return

    # 空库，或已有成员但尚未建立任何白名单：允许完成首次初始化绑定
    if member_count(db) == 0 or not system_has_channel_bindings(db):
        return

    if channel and external_user_id:
        caller = resolve_member_by_binding(db, channel, external_user_id)
        if caller is None:
            raise HTTPException(
                status_code=403,
                detail=f"渠道 {channel} 的外部用户 {external_user_id} 未绑定任何家庭成员，不能执行绑定",
            )
        if caller.id == target_member_id or caller.role == "owner":
            return
        raise HTTPException(status_code=403, detail="只能为自己绑定渠道，或由 owner 代为绑定")

    raise HTTPException(
        status_code=403,
        detail="匿名不可绑定：白名单已建立，请使用已绑定渠道身份或 X-Setup-Token",
    )


def channel_headers(
    x_channel: str | None = Header(default=None, alias="X-Channel"),
    x_external_user_id: str | None = Header(default=None, alias="X-External-User-Id"),
    x_setup_token: str | None = Header(default=None, alias="X-Setup-Token"),
) -> ChannelIdentity:
    return ChannelIdentity(
        member_id=None,
        channel=_normalize_header(x_channel),
        external_user_id=_normalize_header(x_external_user_id),
        setup_token=_normalize_header(x_setup_token),
    )