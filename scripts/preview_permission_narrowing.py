#!/usr/bin/env python3
"""权限阶段 0：渠道缩权/高风险 Grant 只读升级预览（基线 §13 迁移实施要求）。

在发布"非 Owner 渠道身份按角色能力集缩权"的行为变更前，列出受影响对象：
1. 非 Owner 成员的渠道绑定（缩权后失去 books:delete、stats:household）；
2. 含高风险 Scope 的 Agent Grant（books:delete / stats:household）。

只读脚本：仅 SELECT，不修改任何数据。
用法：
    python3 scripts/preview_permission_narrowing.py [--database URL|--db-file PATH] [--json]

数据库定位顺序：--database > --db-file > $DATABASE_URL > backend/data/bookshelf.db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

# app/__init__.py 为空、app/services 为命名空间包：只加载本模块，不触发 app.config
from app.services.permission_policy import HIGH_RISK_SCOPES, MEMBER_ROLE_SCOPES, OWNER_ROLE_SCOPES  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402


def resolve_database_url(args: argparse.Namespace) -> str:
    if args.database:
        return args.database
    if args.db_file:
        return f"sqlite:///{Path(args.db_file).resolve().as_posix()}"
    import os
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    default = _BACKEND_DIR / "data" / "bookshelf.db"
    if not default.exists():
        print(f"错误：未找到默认数据库 {default}，请用 --database 或 --db-file 指定", file=sys.stderr)
        raise SystemExit(2)
    return f"sqlite:///{default.as_posix()}"


def collect_preview(database_url: str) -> dict:
    """只读扫描受影响绑定与高风险 Grant。"""
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            members = conn.execute(text(
                "SELECT id, name, role, channel_bindings FROM members ORDER BY id"
            )).mappings().all()

            affected: list[dict] = []
            for m in members:
                bindings = m["channel_bindings"]
                if not bindings:
                    continue
                try:
                    parsed = json.loads(bindings)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
                if not (isinstance(parsed, dict) and any(str(v).strip() for v in parsed.values() if v is not None)):
                    continue
                if m["role"] == "owner":
                    continue  # Owner 渠道保持全量，不受缩权影响
                affected.append({
                    "member_id": m["id"],
                    "name": m["name"],
                    "role": m["role"],
                    "channels": {k: v for k, v in parsed.items() if v is not None and str(v).strip()},
                    "scopes_lost": sorted(OWNER_ROLE_SCOPES - MEMBER_ROLE_SCOPES),
                })

            high_risk_grants: list[dict] = []
            rows = conn.execute(text("""
                SELECT g.id, g.agent_client_id, c.display_name AS client_name,
                       g.member_id, g.scopes_json, g.status, g.expires_at
                FROM agent_grants g
                LEFT JOIN agent_clients c ON c.id = g.agent_client_id
                ORDER BY g.id
            """)).mappings().all()
            for g in rows:
                try:
                    scopes = json.loads(g["scopes_json"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    scopes = []
                hit = sorted(set(scopes) & set(HIGH_RISK_SCOPES))
                if hit:
                    high_risk_grants.append({
                        "grant_id": g["id"],
                        "agent_client_id": g["agent_client_id"],
                        "client_name": g["client_name"],
                        "member_id": g["member_id"],
                        "high_risk_scopes": hit,
                        "status": g["status"],
                        "expires_at": str(g["expires_at"]) if g["expires_at"] is not None else None,
                    })
    finally:
        engine.dispose()

    return {
        "affected_channel_identities": affected,
        "high_risk_grants": high_risk_grants,
        "summary": {
            "affected_channel_identities": len(affected),
            "high_risk_grants": len(high_risk_grants),
            "member_scopes_lost": sorted(OWNER_ROLE_SCOPES - MEMBER_ROLE_SCOPES),
            "high_risk_scope_names": sorted(HIGH_RISK_SCOPES),
        },
    }


def render_text(preview: dict) -> str:
    lines: list[str] = []
    s = preview["summary"]
    lines.append("== 权限缩权升级预览（只读） ==")
    lines.append(
        f"非 Owner 渠道绑定受影响：{s['affected_channel_identities']} 个；"
        f"含高风险 Scope 的 Grant：{s['high_risk_grants']} 个"
    )
    lines.append(f"Member 能力集将失去：{', '.join(s['member_scopes_lost'])}")
    if preview["affected_channel_identities"]:
        lines.append("")
        lines.append("-- 受影响渠道绑定 --")
        for it in preview["affected_channel_identities"]:
            channels = ", ".join(f"{k}={v}" for k, v in it["channels"].items())
            lines.append(
                f"  成员#{it['member_id']} {it['name']} (role={it['role']}) [{channels}] "
                f"→ 失去 {', '.join(it['scopes_lost'])}"
            )
    if preview["high_risk_grants"]:
        lines.append("")
        lines.append("-- 含高风险 Scope 的 Grant（books:delete / stats:household）--")
        for g in preview["high_risk_grants"]:
            lines.append(
                f"  Grant#{g['grant_id']} client={g['client_name'] or g['agent_client_id']} "
                f"member={g['member_id']} status={g['status']} "
                f"high_risk={', '.join(g['high_risk_scopes'])} expires_at={g['expires_at']}"
            )
    lines.append("")
    lines.append("说明：渠道缩权为既定安全收紧（权限基线 §13），不提供恢复非 Owner 全量能力的开关；")
    lines.append("受影响的自动化流程请改用 Owner Web 会话或单独申请含高风险 Scope 的 Agent Grant。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="权限缩权只读升级预览")
    parser.add_argument("--database", help="SQLAlchemy 数据库 URL（优先）")
    parser.add_argument("--db-file", help="SQLite 文件路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    url = resolve_database_url(args)
    preview = collect_preview(url)
    if args.json:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    else:
        print(render_text(preview))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
