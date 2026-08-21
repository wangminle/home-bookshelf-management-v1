"""preview_permission_narrowing.py 单元测试：只读升级预览（权限阶段 0，基线 §13）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import preview_permission_narrowing as ppn  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402


@pytest.fixture()
def seed_db(tmp_path: Path) -> str:
    """建最小 members/agent_grants/agent_clients 表并播种。"""
    url = f"sqlite:///{(tmp_path / 'preview.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE members (
                id INTEGER PRIMARY KEY, name TEXT, role TEXT,
                channel_bindings TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE agent_clients (
                id INTEGER PRIMARY KEY, display_name TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE agent_grants (
                id INTEGER PRIMARY KEY, agent_client_id INTEGER,
                member_id INTEGER, scopes_json TEXT, status TEXT,
                expires_at TEXT
            )
        """))
        conn.execute(text(
            "INSERT INTO members (name, role, channel_bindings) VALUES "
            "('爸爸', 'owner', :b1), ('妈妈', 'member', :b2), ('孩子', 'member', NULL), "
            "('坏数据', 'member', 'not-json')"
        ), {
            "b1": json.dumps({"feishu": "ou_owner"}),
            "b2": json.dumps({"feishu": "ou_member"}),
        })
        conn.execute(text(
            "INSERT INTO agent_clients (display_name) VALUES ('扫地 Agent'), ('查书 Agent')"
        ))
        conn.execute(text(
            "INSERT INTO agent_grants (agent_client_id, member_id, scopes_json, status, expires_at) VALUES "
            "(1, 2, :s1, 'active', '2026-09-01 00:00:00'), "
            "(2, 1, :s2, 'revoked', '2026-08-25 00:00:00')"
        ), {
            "s1": json.dumps(["books:read", "books:delete"]),
            "s2": json.dumps(["books:read"]),
        })
    engine.dispose()
    return url


def test_preview_flags_non_owner_bindings_and_high_risk_grants(seed_db: str) -> None:
    preview = ppn.collect_preview(seed_db)
    affected = preview["affected_channel_identities"]
    # 只有 role=member 且绑定非空的"妈妈"受影响；owner/无绑定/坏 JSON 不受影响
    assert [a["name"] for a in affected] == ["妈妈"]
    assert affected[0]["scopes_lost"] == ["books:delete", "stats:household"]
    assert affected[0]["channels"] == {"feishu": "ou_member"}

    grants = preview["high_risk_grants"]
    assert len(grants) == 1
    assert grants[0]["grant_id"] == 1
    assert grants[0]["high_risk_scopes"] == ["books:delete"]
    assert grants[0]["client_name"] == "扫地 Agent"

    assert preview["summary"]["affected_channel_identities"] == 1
    assert preview["summary"]["high_risk_grants"] == 1


def test_preview_is_read_only(seed_db: str) -> None:
    """脚本不得写入任何数据（快照前后行数一致）。"""
    ppn.collect_preview(seed_db)
    engine = create_engine(seed_db)
    with engine.connect() as conn:
        for table in ("members", "agent_clients", "agent_grants"):
            n = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            assert n > 0, table
    engine.dispose()


def test_render_text_mentions_migration_guidance(seed_db: str) -> None:
    out = ppn.render_text(ppn.collect_preview(seed_db))
    assert "妈妈" in out
    assert "books:delete" in out
    assert "不提供恢复非 Owner 全量能力的开关" in out


def test_scope_constants_match_policy() -> None:
    """脚本输出的失去能力清单必须与后端策略模块一致（防漂移）。"""
    from app.services import permission_policy
    assert set(ppn.MEMBER_ROLE_SCOPES) == set(permission_policy.MEMBER_ROLE_SCOPES)
    assert set(ppn.OWNER_ROLE_SCOPES) == set(permission_policy.OWNER_ROLE_SCOPES)
    assert set(ppn.HIGH_RISK_SCOPES) == set(permission_policy.HIGH_RISK_SCOPES)
    assert sorted(ppn.OWNER_ROLE_SCOPES - ppn.MEMBER_ROLE_SCOPES) == ["books:delete", "stats:household"]


def test_main_json_output(seed_db: str, capsys) -> None:
    rc = ppn.main(["--database", seed_db, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["affected_channel_identities"] == 1
