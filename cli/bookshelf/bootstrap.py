"""WBS-8：CLI bootstrap 命令。

Agent 通过此命令发现系统能力，无需业务权限。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import typer

from bookshelf.credentials import (
    TokenFileError,
    delete_token,
    resolve_token,
    save_token,
    select_token_file,
    token_file_path,
    validate_token,
)


def _discovery_url(base_url: str, path: str) -> str:
    """构建发现面 URL（不走 /api/v1 前缀）。"""
    return f"{base_url.rstrip('/')}{path}"


def _json_or_none(resp: httpx.Response) -> Any | None:
    """解析 JSON 响应体；200 但非 JSON（如指向了错误服务）返回 None 而非抛异常。"""
    try:
        return resp.json()
    except ValueError:
        return None


def cmd_bootstrap(
    url: str = typer.Argument(..., help="服务端地址，如 http://127.0.0.1:8000"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
) -> None:
    """发现系统契约：获取 manifest、bootstrap.md、skills 索引。

    不需要任何认证，只访问公开发现面。
    """
    result: dict[str, Any] = {"url": url, "ok": False}

    try:
        with httpx.Client(timeout=10.0) as client:
            # 获取 manifest
            resp = client.get(_discovery_url(url, "/agent/manifest.json"))
            if resp.status_code == 200:
                # 200 但非 JSON（如地址指向了其它服务）不算发现成功
                manifest = _json_or_none(resp)
                if manifest is None:
                    result["manifest_error"] = "HTTP 200 但响应非 JSON（地址可能指向其它服务）"
                else:
                    result["manifest"] = manifest
                    result["ok"] = True
            else:
                result["manifest_error"] = f"HTTP {resp.status_code}"

            # 获取 skills 索引
            resp = client.get(_discovery_url(url, "/agent/skills/index.json"))
            if resp.status_code == 200:
                skills_index = _json_or_none(resp)
                if skills_index is None:
                    result["skills_error"] = "HTTP 200 但响应非 JSON"
                else:
                    result["skills_index"] = skills_index
            else:
                result["skills_error"] = f"HTTP {resp.status_code}"

            # 获取 public health
            resp = client.get(_discovery_url(url, "/api/v1/public-health"))
            if resp.status_code == 200:
                health = _json_or_none(resp)
                if health is None:
                    result["health_error"] = "HTTP 200 但响应非 JSON"
                else:
                    result["health"] = health
            else:
                result["health_error"] = f"HTTP {resp.status_code}"

    except httpx.HTTPError as exc:
        result["error"] = f"连接失败: {exc.__class__.__name__}"
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"❌ 连接失败: {exc}")
        raise typer.Exit(code=1)

    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("ok"):
            manifest = result.get("manifest", {})
            service = manifest.get("service", {})
            print(f"✅ 连接成功")
            print(f"  服务: {service.get('name', '?')} v{service.get('version', '?')}")
            data_policy = manifest.get("data_policy", {})
            print(f"  发现面不含业务数据: {not data_policy.get('discovery_contains_business_data', True)}")
            print(f"  认证方式: {data_policy.get('authentication', '?')}")
            capabilities = manifest.get("capabilities", [])
            if capabilities:
                print(f"  能力数: {len(capabilities)}")
            skills = result.get("skills_index", {})
            skill_list = skills.get("skills", [])
            if skill_list:
                print(f"  Skills 数: {len(skill_list)}")
                for s in skill_list:
                    scopes_str = ", ".join(s.get("scopes", [])) or "(无需授权)"
                    print(f"    - {s['name']}: {s.get('description', '')} [{scopes_str}]")
            print()
            print("下一步:")
            print("  1. 向 Owner 申请 Agent Token")
            print("  2. 保存 Token: bookshelf auth login（隐藏输入，0600 文件）或 export BOOKSHELF_TOKEN=<token>")
            print("  3. 运行: bookshelf auth status  验证授权")
            print("  4. 运行: bookshelf doctor --authorized  检查业务连通性")
        else:
            print("❌ 发现失败")
            if result.get("manifest_error"):
                print(f"  Manifest: {result['manifest_error']}")
            if result.get("skills_error"):
                print(f"  Skills: {result['skills_error']}")


def collect_auth_status() -> dict[str, Any]:
    """构造授权状态结果（不打印、不退出）。

    供 `bookshelf auth status` 与 `bookshelf doctor --authorized` 复用——
    后者需要把 auth 结果并入单一 JSON 文档，不能再各自 print。
    """
    base_url = os.environ.get("BOOKSHELF_API_URL", "http://127.0.0.1:8000")
    result: dict[str, Any] = {"has_token": False, "base_url": base_url}

    try:
        resolved = resolve_token()
    except TokenFileError as exc:
        # DEV-036：文件缺失/权限过宽/内容非法——如实报告，不退回到"未设置"
        result["status"] = "token_file_error"
        result["message"] = str(exc)
        return result

    if resolved is None:
        result["status"] = "unauthorized"
        result["message"] = (
            "未设置 BOOKSHELF_TOKEN 环境变量，也未找到 Token 文件"
            f"（BOOKSHELF_TOKEN_FILE 或默认 {token_file_path()}）"
        )
        return result

    result["has_token"] = True
    result["token_source"] = resolved.source
    if resolved.path is not None:
        result["token_file"] = str(resolved.path)
    return _introspect(result, resolved.token, base_url)


def verify_token(token: str, base_url: str | None = None) -> dict[str, Any]:
    """用给定 Token 调用 introspect，返回与 collect_auth_status 同形的结果（不读取任何存储）。"""
    url = base_url or os.environ.get("BOOKSHELF_API_URL", "http://127.0.0.1:8000")
    return _introspect({"has_token": True, "base_url": url}, token, url)


def _introspect(result: dict[str, Any], token: str, base_url: str) -> dict[str, Any]:
    # 使用 token 调用 introspect 端点验证（真正验证 Token + 返回 scope 信息）
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                f"{base_url.rstrip('/')}/auth/introspect",
                headers={"Authorization": f"Bearer {token}"},
            )
            result["http_status"] = resp.status_code
            if resp.status_code == 200:
                body = _json_or_none(resp)
                if not isinstance(body, dict):
                    # 200 非 JSON：地址可能指向了错误的服务，不能当授权成功
                    result["status"] = "error"
                    result["message"] = "HTTP 200 但响应非 JSON（地址可能指向其它服务）"
                    return result
                result["status"] = "authorized"
                result["message"] = "Token 有效"
                result["scopes"] = body.get("scopes", [])
                result["client_name"] = body.get("client_name")
                result["member_name"] = body.get("member_name")
            elif resp.status_code == 401:
                result["status"] = "invalid_token"
                result["message"] = "Token 无效或已过期"
            elif resp.status_code == 403:
                result["status"] = "insufficient_scope"
                result["message"] = "Token 有效但缺少所需 scope"
            else:
                result["status"] = "error"
                result["message"] = f"HTTP {resp.status_code}"
    except httpx.HTTPError:
        result["status"] = "connection_error"
        result["message"] = "连接失败（详见 doctor）"
    return result


def emit_auth_status(result: dict[str, Any], json_output: bool) -> None:
    """输出授权状态结果（不负责退出码）。"""
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result["status"] == "unauthorized":
        print("❌ 未授权")
        print(f"  {result['message']}")
        print("  请向 Owner 申请 Agent Token 后，任选其一（推荐前者，Token 不出现在命令行）:")
        print("  bookshelf auth login        # 隐藏输入，保存到 0600 文件")
        print("  export BOOKSHELF_TOKEN=<your_token>")
        return
    status_emoji = {"authorized": "✅", "invalid_token": "❌", "insufficient_scope": "⚠️", "error": "❌",
                    "token_file_error": "❌"}.get(result["status"], "?")
    print(f"{status_emoji} 授权状态: {result['status']}")
    print(f"  {result['message']}")
    if result.get("token_source"):
        where = f"文件 {result['token_file']}" if result.get("token_file") else "环境变量 BOOKSHELF_TOKEN"
        print(f"  Token 来源: {where}")
    if result.get("client_name"):
        print(f"  Agent: {result['client_name']}")
    if result.get("member_name"):
        print(f"  成员: {result['member_name']}")
    if result.get("scopes"):
        print(f"  Scope: {', '.join(result['scopes'])}")


def cmd_auth_status(
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
) -> None:
    """检查当前 Agent 授权状态。

    Token 来源：BOOKSHELF_TOKEN > BOOKSHELF_TOKEN_FILE > login --file 保存的路径 > 默认文件；
    不打印 Token 明文。
    """
    result = collect_auth_status()
    emit_auth_status(result, json_output)
    if result["status"] != "authorized":
        raise typer.Exit(code=1)


def _fail_login(result: dict[str, Any], json_output: bool, message: str) -> None:
    result["ok"] = False
    result.setdefault("saved", False)
    result["message"] = message
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.secho(f"❌ {message}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


def cmd_auth_login(
    use_stdin: bool, file: Path | None, verify: bool, json_output: bool,
) -> None:
    """保存 Agent Token 到 0600 文件（DEV-036）。

    Token 只从标准输入或隐藏提示读取，没有 --token 参数，因此不会进入
    命令行、进程列表或 shell 历史。默认先 introspect 验证，通过才落盘。
    """
    result: dict[str, Any] = {"ok": False, "saved": False}
    try:
        path = Path(file).expanduser() if file else token_file_path()
    except TokenFileError as exc:
        _fail_login(result, json_output, str(exc))
        return
    result["path"] = str(path)
    if use_stdin:
        raw = sys.stdin.read()
    else:
        raw = typer.prompt("Agent Token（输入不回显）", hide_input=True, default="", show_default=False, err=True)
    try:
        token = validate_token(raw)
    except TokenFileError as exc:
        _fail_login(result, json_output, str(exc))
        return

    if verify:
        checked = verify_token(token)
        result["status"] = checked["status"]
        result["base_url"] = checked["base_url"]
        if checked["status"] != "authorized":
            _fail_login(result, json_output, f"Token 校验未通过（{checked['status']}）：{checked.get('message', '')}，未保存")
            return
        result["scopes"] = checked.get("scopes", [])
        result["client_name"] = checked.get("client_name")
    try:
        save_token(token, path)
        if file:
            select_token_file(path)
    except TokenFileError as exc:
        _fail_login(result, json_output, str(exc))
        return
    result["ok"] = True
    result["saved"] = True
    if os.environ.get("BOOKSHELF_TOKEN"):
        result["note"] = "当前环境仍设置了 BOOKSHELF_TOKEN，它优先于文件；如需改用文件请 unset BOOKSHELF_TOKEN"
    elif file and os.environ.get("BOOKSHELF_TOKEN_FILE"):
        result["note"] = "已记住 --file 路径；当前 BOOKSHELF_TOKEN_FILE 仍优先，请取消该变量后使用保存的路径"
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"✅ Token 已保存到 {path}（权限 0600）")
        if result.get("scopes"):
            print(f"  Scope: {', '.join(result['scopes'])}")
        if result.get("note"):
            print(f"  ⚠ {result['note']}")


def cmd_auth_logout(file: Path | None, json_output: bool) -> None:
    """删除 Token 文件（幂等）。不影响 BOOKSHELF_TOKEN 环境变量。"""
    result: dict[str, Any] = {"removed": False}
    try:
        path = Path(file).expanduser() if file else token_file_path()
        result["path"] = str(path)
        removed = delete_token(path)
    except TokenFileError as exc:
        _fail_login(result, json_output, str(exc))
        return
    result: dict[str, Any] = {"ok": True, "removed": removed, "path": str(path)}
    if os.environ.get("BOOKSHELF_TOKEN"):
        result["note"] = "环境变量 BOOKSHELF_TOKEN 仍然设置，需自行 unset"
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 已删除 {path}" if removed else f"Token 文件不存在，无需删除：{path}")
        if result.get("note"):
            print(f"  ⚠ {result['note']}")
