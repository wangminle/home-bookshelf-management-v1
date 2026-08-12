"""WBS-5：管理 CLI 命令。

提供 owner 密码初始化与重置命令，供 install.sh 或管理员手动调用。

用法：
    python -m app.admin owner-init-password   # 首次设置密码（交互式）
    python -m app.admin owner-reset-password   # 重置密码（交互式，需确认）
    python -m app.admin owner-status            # 查看密码状态
"""
from __future__ import annotations

import getpass
import sys

from app.db import SessionLocal
from app.services import agent_access


def _read_password(confirm: bool = True) -> str:
    pw = getpass.getpass("请输入新密码（≥8 位）: ")
    if len(pw) < 8:
        print("错误：密码至少 8 位", file=sys.stderr)
        sys.exit(1)
    if confirm:
        pw2 = getpass.getpass("请再次输入: ")
        if pw != pw2:
            print("错误：两次输入不一致", file=sys.stderr)
            sys.exit(1)
    return pw


def cmd_owner_init_password() -> None:
    """首次设置 owner 密码。"""
    with SessionLocal() as db:
        if agent_access.has_owner_password(db):
            print("Owner 密码已设置。如需重置请使用 owner-reset-password。", file=sys.stderr)
            sys.exit(1)
        pw = _read_password()
        agent_access.set_owner_password(db, pw)
        print("✅ Owner 密码已设置。")


def cmd_owner_reset_password() -> None:
    """重置 owner 密码。"""
    with SessionLocal() as db:
        if not agent_access.has_owner_password(db):
            print("Owner 密码尚未设置。请使用 owner-init-password。", file=sys.stderr)
            sys.exit(1)
        print("⚠️  即将重置 Owner 密码，所有已登录 Web 会话不会自动失效。")
        confirm = input("确认重置？输入 yes 继续: ")
        if confirm.strip().lower() != "yes":
            print("已取消。")
            return
        pw = _read_password()
        agent_access.set_owner_password(db, pw)
        print("✅ Owner 密码已重置。")


def cmd_owner_status() -> None:
    """查看 owner 密码状态。"""
    with SessionLocal() as db:
        initialized = agent_access.has_owner_password(db)
        owner = agent_access.get_owner_member(db)
        if owner is None:
            print("❌ 系统中尚无 owner 成员。")
        else:
            print(f"Owner 成员: {owner.name} (id={owner.id})")
            print(f"密码已设置: {'是' if initialized else '否'}")


_COMMANDS = {
    "owner-init-password": cmd_owner_init_password,
    "owner-reset-password": cmd_owner_reset_password,
    "owner-status": cmd_owner_status,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        print(f"用法: python -m app.admin <command>\n\n可用命令:\n  " +
              "\n  ".join(_COMMANDS.keys()), file=sys.stderr)
        sys.exit(1)
    _COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
