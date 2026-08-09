from __future__ import annotations

import hashlib
import hmac
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
    signature: str | None = None
    # BUG-134：内置 Web UI 通过 X-UI-Client: web 标识自身，
    # 白名单建立后仍允许受信 UI 匿名写入（回退到默认成员）。
    ui_client: bool = False


def _normalize_header(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def expected_channel_signature(channel: str, external_user_id: str, secret: str) -> str:
    """渠道身份签名：HMAC-SHA256(secret, "{channel}:{external_user_id}") 十六进制。"""
    msg = f"{channel}:{external_user_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _verify_channel_signature(
    channel: str | None,
    external_user_id: str | None,
    signature: str | None,
) -> None:
    """BUG-132：配置共享密钥后，携带渠道头的请求必须带有效 HMAC 签名。

    未配置密钥时维持"可信局域网/网关代填头"边界，不做校验。
    仅在渠道头成对出现时校验；无渠道头的匿名请求由 enforce_channel_member 另行裁决。
    """
    secret = _normalize_header(settings.channel_signing_secret)
    if not secret or not (channel and external_user_id):
        return
    if not signature:
        raise HTTPException(
            status_code=403,
            detail="已启用渠道签名校验，请提供 X-Channel-Signature",
        )
    expected = expected_channel_signature(channel, external_user_id, secret)
    if not hmac.compare_digest(signature.strip().lower(), expected):
        raise HTTPException(status_code=403, detail="渠道签名无效")


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
    ui_client: bool = False,
) -> int:
    """渠道身份鉴权：

    - 渠道头必须成对；只传一个 → 400。
    - 无渠道头：
      - require_channel=True 或系统已建立渠道绑定 -> 403，拒绝匿名（BUG-113）。
      - 否则（系统尚无任何绑定，仍处初始化引导期）-> 回退到 resolve_member_id（一期可信局域网兜底）。
    - 有渠道头但未绑定：返回 403，拒绝冒用。
    - 有渠道头且已绑定：以绑定成员为准；若 body 同时指定了不同的 member_id，则 403。

    BUG-113：白名单（渠道绑定）建立后，所有写操作必须提供已绑定渠道头，
    不再允许匿名回退，杜绝已知外部 ID 即可冒充 owner。
    BUG-134：内置 Web UI 无法发送渠道头，通过 X-UI-Client: web 标识后
    仍允许回退到默认成员，维持可信局域网 UI 可用性。
    """
    channel = _normalize_header(channel)
    external_user_id = _normalize_header(external_user_id)
    require_complete_channel_headers(channel, external_user_id)

    if not channel and not external_user_id:
        # 显式 require_channel 或系统已建立白名单时，拒绝匿名写
        bindings_established = system_has_channel_bindings(db)
        if (require_channel or bindings_established) and not ui_client:
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
    x_channel_signature: str | None = Header(default=None, alias="X-Channel-Signature"),
    x_ui_client: str | None = Header(default=None, alias="X-UI-Client"),
) -> ChannelIdentity:
    channel = _normalize_header(x_channel)
    external_user_id = _normalize_header(x_external_user_id)
    signature = _normalize_header(x_channel_signature)
    # BUG-132：统一在这里校验渠道签名，所有使用该依赖的端点都被覆盖
    _verify_channel_signature(channel, external_user_id, signature)
    # BUG-134：内置 Web UI 通过 X-UI-Client: web 标识自身
    ui_client = _normalize_header(x_ui_client) == "web"
    return ChannelIdentity(
        member_id=None,
        channel=channel,
        external_user_id=external_user_id,
        setup_token=_normalize_header(x_setup_token),
        signature=signature,
        ui_client=ui_client,
    )