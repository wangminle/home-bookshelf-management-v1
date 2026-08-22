"""BUG-203/204 修复回归：登录用户名大小写不敏感唯一与生成器 exclude_id。

- BUG-204：lower(username) 唯一索引——Zhang/zhang 不可并存（API 409 + DB 层
  IntegrityError 双保险）；登录解析单次 CI 查询无歧义；
- BUG-203：ensure_unique_username 的 exclude_id 生效（改名场景排除自身）；
- 死代码清理后 set_owner_password/reset 端点不再重复补用户名（服务层统一兜底）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Member
from app.services.agent_access import ensure_unique_username


def test_create_member_rejects_case_insensitive_duplicate(client: TestClient) -> None:
    r = client.post("/api/v1/members", json={"name": "张三", "role": "member", "username": "zhang"})
    assert r.status_code == 201, r.text
    # 大小写变体 → 409（CI 唯一口径）
    r = client.post("/api/v1/members", json={"name": "张三二号", "role": "member", "username": "Zhang"})
    assert r.status_code == 409
    assert "用户名" in r.text


def test_db_level_case_insensitive_unique(db_session: Session, client: TestClient) -> None:
    """绕过 API 预检直插数据库：CI 唯一索引必须兜底拦截。"""
    db_session.add(Member(name="甲", role="member", username="zhang"))
    db_session.commit()
    db_session.add(Member(name="乙", role="member", username="ZHaNG"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_login_resolves_case_insensitively(client: TestClient, db_session: Session) -> None:
    r = client.post("/api/v1/members", json={"name": "李四", "role": "member", "username": "Lisi"})
    assert r.status_code == 201
    member_id = r.json()["data"]["id"]
    r = client.post(f"/api/v1/members/{member_id}/password", json={"password": "lisi-pass-12345"})

    for variant in ("Lisi", "lisi", "LISI"):
        r = client.post("/auth/login", json={"username": variant, "password": "lisi-pass-12345"})
        assert r.status_code == 200, f"{variant}: {r.text}"
        assert r.json()["member_id"] == member_id


def test_ensure_unique_username_ci_and_exclude(db_session: Session) -> None:
    """BUG-203/204：生成器按小写去重，且 exclude_id 排除自身现有用户名。"""
    existing = Member(name="王五", role="member", username="wang")
    other = Member(name="赵六", role="member", username="zhao")
    db_session.add_all([existing, other])
    db_session.commit()

    # CI 冲突 → 追加序号
    assert ensure_unique_username(db_session, "wang") == "wang_2"
    assert ensure_unique_username(db_session, "WANG") == "WANG_2"
    # exclude_id 排除自身：为 existing 改名时 "wang" 可复用
    assert ensure_unique_username(db_session, "wang", exclude_id=existing.id) == "wang"
    # 不排除时 "zhao" 被占用
    assert ensure_unique_username(db_session, "zhao", exclude_id=existing.id) == "zhao_2"


def test_owner_password_init_assigns_username_ci_unique(client: TestClient, db_session: Session) -> None:
    """同名成员存在时，owner 初始化密码生成的用户名按 CI 去重。"""
    owner_id = client.get("/auth/session").json()["member_id"]
    owner = db_session.get(Member, owner_id)
    owner_name = owner.name
    # 造一个与 owner 同显示名的成员并占用同名用户名
    db_session.add(Member(name=owner_name, role="member", username=owner_name.lower()))
    db_session.commit()

    from app.services import agent_access
    agent_access.set_owner_password(db_session, "init-pass-123456")
    db_session.refresh(owner)
    assert owner.username and owner.username.lower() != owner_name.lower()
