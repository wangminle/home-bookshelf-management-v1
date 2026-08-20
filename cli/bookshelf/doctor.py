from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from bookshelf.client import BookshelfClient, DEFAULT_API_URL


@dataclass
class DoctorReport:
    api_url: str
    api_reachable: bool = False
    api_http_status: int | None = None
    # BUG-167 后 /health 需认证：无凭证时仅能经 public-health 验证可达，DB 状态未知（None）
    db_ok: bool | None = False
    google_books_configured: bool = False
    barcode_scan_available: bool = False
    members_total: int = 0
    members_bound: int = 0
    members: list[dict[str, Any]] = field(default_factory=list)
    bookshelf_api_url_from_env: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.api_reachable and self.db_ok is not False and not self.errors

    def to_payload(self) -> dict[str, Any]:
        message = "初始化检查通过，可以开始使用藏书功能" if self.ready else "初始化检查未通过，请按 errors/warnings 处理"
        return {
            "ok": self.ready,
            "data": {
                "checks": {
                    "api_url": self.api_url,
                    "bookshelf_api_url_from_env": self.bookshelf_api_url_from_env,
                    "api_reachable": self.api_reachable,
                    "api_http_status": self.api_http_status,
                    "db_ok": self.db_ok,
                    "google_books_configured": self.google_books_configured,
                    "barcode_scan_available": self.barcode_scan_available,
                    "members_total": self.members_total,
                    "members_bound": self.members_bound,
                },
                "members": self.members,
                "errors": self.errors,
                "warnings": self.warnings,
                "hints": self.hints,
                "ready": self.ready,
                "message": message,
            },
        }


def _skills_dir_hint() -> str | None:
    env_dir = os.environ.get("BOOKSHELF_SKILLS_DIR")
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    cwd = os.getcwd()
    for candidate in (
        os.path.join(cwd, "skills"),
        os.path.join(cwd, "..", "skills"),
        os.path.join(cwd, "home-bookshelf-management-v1", "skills"),
    ):
        resolved = os.path.abspath(candidate)
        if os.path.isdir(resolved) and os.path.isfile(os.path.join(resolved, "README.md")):
            return resolved
    return None


def run_doctor(client: BookshelfClient | None = None) -> DoctorReport:
    api_url = (client.base_url if client else os.environ.get("BOOKSHELF_API_URL", DEFAULT_API_URL)).rstrip("/")
    report = DoctorReport(
        api_url=api_url,
        bookshelf_api_url_from_env=bool(os.environ.get("BOOKSHELF_API_URL")),
    )
    probe_client = client or BookshelfClient(base_url=api_url)

    # BUG-167：/health 已要求认证；client.health 在无凭证时自动回退 public-health，
    # 返回体带 auth_protected=True、database="unknown"，诊断细节（DB/Key/条码）不可得。
    try:
        health_payload = probe_client.health()
    except RuntimeError as exc:
        report.errors.append(str(exc))
        report.hints.append("本机启动：cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000")
        report.hints.append("Docker：cd deploy && docker compose up -d")
        return report

    report.api_reachable = True
    report.api_http_status = health_payload.get("_http_status")
    health_data = health_payload.get("data") if isinstance(health_payload, dict) else None
    if not isinstance(health_data, dict):
        report.errors.append("health 响应格式异常")
        return report

    if health_data.get("auth_protected"):
        report.db_ok = None
        report.warnings.append(
            "/health 需要认证（BOOKSHELF_TOKEN 无 members:read 或未设置），本次仅验证可达性（public-health），数据库/依赖诊断不可用"
        )
        report.hints.append("完整诊断：在前端 Agent 授权页签发含 members:read 的 Token，export BOOKSHELF_TOKEN=... 后重试")
    else:
        report.db_ok = health_data.get("database") == "connected"
        if not report.db_ok:
            report.errors.append("数据库未连接（/health 返回 database=disconnected）")
            report.hints.append("检查 DATABASE_URL / data/ 目录权限，并运行 alembic upgrade head")

        report.google_books_configured = bool(health_data.get("google_books_configured"))
        if not report.google_books_configured:
            report.warnings.append(
                "服务端未配置 GOOGLE_BOOKS_API_KEY，中文书 metadata 命中率可能下降（OpenLibrary/国图仍可用）"
            )
            report.hints.append("在 deploy/.env 或 backend/.env 设置 GOOGLE_BOOKS_API_KEY 后重启 API")

        report.barcode_scan_available = bool(health_data.get("barcode_scan_available"))
        if not report.barcode_scan_available:
            report.warnings.append("服务端未安装条码识别依赖（pyzbar/Pillow 或系统 libzbar0）")
            report.hints.append("macOS: brew install zbar；Docker 镜像已含 libzbar0")

    try:
        members_payload = probe_client.members()
        items = (members_payload.get("data") or {}).get("items") or []
        report.members = items
        report.members_total = len(items)
        report.members_bound = sum(1 for m in items if m.get("channel_bindings"))
        if report.members_bound == 0:
            report.warnings.append("尚无成员绑定 IM 渠道（members.channel_bindings 为空）")
            report.hints.append("空库首次绑定：bookshelf bind --member-id 1 --channel feishu --external-user-id ou_xxx")
            report.hints.append("或先创建成员再绑：bookshelf member --name \"你\" --role owner；然后 bookshelf bind --member-id 1 ...（系统尚无绑定时允许首次初始化）")
            report.hints.append("白名单建立后新增绑定：使用已绑定 owner 的 BOOKSHELF_CHANNEL/BOOKSHELF_EXTERNAL_USER_ID，或设置 BOOKSHELF_SETUP_TOKEN")
    except RuntimeError as exc:
        msg = str(exc)
        if "[HTTP 404]" in msg or msg.strip().endswith("404"):
            report.warnings.append("API 可能未更新到最新版本（缺少 GET /members），请拉取代码并重启后端")
        elif "[HTTP 401]" in msg or "[HTTP 403]" in msg:
            # BUG-167：GET /members 需 members:read，无凭证时属预期而非故障
            report.warnings.append("GET /members 需要认证（members:read），未读取成员/绑定状态")
            report.hints.append("export BOOKSHELF_TOKEN=...（含 members:read 的 Grant）后重试，或使用已绑定渠道身份")
        else:
            report.warnings.append(f"无法读取成员列表：{exc}")

    if not report.bookshelf_api_url_from_env and api_url == DEFAULT_API_URL:
        report.hints.append("Agent/CLI 连远程服务器时：export BOOKSHELF_API_URL=http://<家庭服务器IP>:8000")

    skills_dir = _skills_dir_hint()
    if skills_dir:
        report.hints.append(f"Skills 目录：{skills_dir}（加入 Agent 可用技能列表）")
    else:
        report.hints.append("将项目 skills/ 目录加入 OpenClaw/Hermes 等 Agent 的技能路径")

    if shutil.which("bookshelf") is None:
        report.warnings.append("当前 shell 未找到 bookshelf 命令（可能未 pip install -e cli）")

    return report


def emit_doctor(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    data = payload.get("data") or {}
    checks = data.get("checks") or {}
    print(data.get("message", "初始化检查"))
    print(f"  API: {checks.get('api_url')} ({'可达' if checks.get('api_reachable') else '不可达'})")
    db_state = checks.get("db_ok")
    print(f"  数据库: {'正常' if db_state else ('未知（/health 需认证）' if db_state is None else '异常')}")
    print(f"  Google Books Key: {'已配置' if checks.get('google_books_configured') else '未配置'}")
    print(f"  条码识别(服务端): {'可用' if checks.get('barcode_scan_available') else '不可用'}")
    print(f"  成员: {checks.get('members_total', 0)} 人，已绑定渠道 {checks.get('members_bound', 0)} 人")

    for label, items in (("错误", data.get("errors")), ("警告", data.get("warnings")), ("提示", data.get("hints"))):
        if not items:
            continue
        print(f"\n{label}:")
        for item in items:
            print(f"  - {item}")
