"""DEV-036：Agent Token 的文件存储与解析。

目标：Token 不必出现在命令行参数或进程列表（`ps`、shell 历史、agent 拼接的命令）。
解析优先级（高→低）：
    1. BOOKSHELF_TOKEN 环境变量（既有行为，保持不变）
    2. BOOKSHELF_TOKEN_FILE 指向的文件（显式指定，缺失即报错，不静默回退）
    3. auth login --file 持久选择的文件（路径配置为同配置目录下 token-path.json）
    4. 默认文件 $XDG_CONFIG_HOME/bookshelf/token 或 ~/.config/bookshelf/token（缺失视为未配置）

安全约束（POSIX）：文件须为普通文件、属主为当前用户、权限不含 group/other 位（即 0600），
否则拒绝读取——与 ssh 私钥同口径。权限检查对已打开的描述符做，避免检查与读取之间被替换。
错误信息与输出绝不包含 Token 内容。
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_TOKEN_BYTES = 8192


class TokenFileError(RuntimeError):
    """Token 文件缺失/权限过宽/内容非法。继承 RuntimeError，CLI 统一转为单行错误。"""


@dataclass(frozen=True)
class ResolvedToken:
    token: str
    source: str  # "env" | "file"
    path: Path | None = None


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg).expanduser() if xdg else Path.home() / ".config") / "bookshelf"


def token_file_path() -> Path:
    explicit = os.environ.get("BOOKSHELF_TOKEN_FILE")
    if explicit:
        return Path(explicit).expanduser()
    saved = _read_private_text(_config_dir() / "token-path.json", explicit=False, label="Token 路径配置文件", private=False)
    if saved is not None:
        try:
            selected = json.loads(saved)["token_file"]
            if not isinstance(selected, str) or not selected or not Path(selected).is_absolute():
                raise ValueError
            return Path(selected)
        except (ValueError, KeyError, TypeError):
            raise TokenFileError("Token 路径配置无效，请重新运行 auth login --file") from None
    return _config_dir() / "token"


def select_token_file(path: Path) -> None:
    """持久保存 --file 选择；环境变量仍有更高优先级。"""
    target = path.expanduser().resolve()
    config = _config_dir() / "token-path.json"
    if target == config:
        raise TokenFileError("Token 文件不能覆盖 token-path.json 路径配置")
    _save_private_text(json.dumps({"token_file": str(target)}, ensure_ascii=False), config, label="Token 路径配置文件")


def validate_token(token: str) -> str:
    """规范化并校验 Token：非空、无任何空白字符（含换行）。"""
    cleaned = token.strip()
    if not cleaned:
        raise TokenFileError("Token 为空")
    if any(ch.isspace() for ch in cleaned):
        raise TokenFileError("Token 格式无效：不能包含空白或换行")
    return cleaned


def _read_private_text(path: Path, *, explicit: bool, label: str = "Token 文件", private: bool = True) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        if explicit:
            raise TokenFileError(f"{label}不存在：{path}（来自 BOOKSHELF_TOKEN_FILE）") from None
        return None
    except OSError as exc:
        raise TokenFileError(f"无法读取 {label} {path}：{exc.strerror or exc.__class__.__name__}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise TokenFileError(f"{label}路径不是普通文件：{path}")
        if os.name != "nt":
            if info.st_uid != os.geteuid():
                raise TokenFileError(f"{label} {path} 不属于当前用户，已拒绝读取")
            if info.st_mode & (0o077 if private else 0o022):
                raise TokenFileError(
                    f"{label} {path} 权限过宽（{stat.S_IMODE(info.st_mode):04o}），"
                    f"已拒绝读取；请执行 chmod {'600' if private else '644'} {path}"
                )
        raw = os.read(fd, MAX_TOKEN_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_TOKEN_BYTES:
        raise TokenFileError(f"{label} {path} 过大，已拒绝读取")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise TokenFileError(f"{label} {path} 不是 UTF-8 文本") from None
    return text


def _read_token_file(path: Path, *, explicit: bool) -> str | None:
    text = _read_private_text(path, explicit=explicit)
    if text is None:
        return None
    try:
        return validate_token(text)
    except TokenFileError as exc:
        raise TokenFileError(f"Token 文件 {path} 内容无效：{exc}") from None


def resolve_token() -> ResolvedToken | None:
    """按优先级解析 Token；都未配置返回 None；文件存在但不可用则抛 TokenFileError。"""
    env_token = os.environ.get("BOOKSHELF_TOKEN")
    if env_token:
        return ResolvedToken(env_token, "env")
    path = token_file_path()
    token = _read_token_file(path, explicit=bool(os.environ.get("BOOKSHELF_TOKEN_FILE")))
    if token is None:
        return None
    return ResolvedToken(token, "file", path)


def save_token(token: str, path: Path | None = None) -> Path:
    """原子写入 Token 文件（0600，新建目录 0700）。已存在文件被替换并收紧为 0600。"""
    cleaned = validate_token(token)
    target = Path(path) if path is not None else token_file_path()
    if target.expanduser().resolve() == (_config_dir() / "token-path.json").resolve():
        raise TokenFileError("Token 文件不能覆盖 token-path.json 路径配置")
    return _save_private_text(cleaned, target)


def _save_private_text(cleaned: str, target: Path, *, label: str = "Token 文件") -> Path:
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = parent / f".{target.name}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(cleaned + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            raise
    except OSError as exc:
        raise TokenFileError(f"无法写入 {label} {target}：{exc.strerror or exc.__class__.__name__}") from None
    return target


def delete_token(path: Path | None = None) -> bool:
    target = Path(path) if path is not None else token_file_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TokenFileError(f"无法删除 Token 文件 {target}：{exc.strerror or exc.__class__.__name__}") from None
    return True
