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
            print("  2. 设置环境变量: export BOOKSHELF_TOKEN=<token>")
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
    token = os.environ.get("BOOKSHELF_TOKEN")
    base_url = os.environ.get("BOOKSHELF_API_URL", "http://127.0.0.1:8000")

    result: dict[str, Any] = {
        "has_token": bool(token),
        "base_url": base_url,
    }

    if not token:
        result["status"] = "unauthorized"
        result["message"] = "未设置 BOOKSHELF_TOKEN 环境变量"
        return result

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
        print("  请向 Owner 申请 Agent Token 后:")
        print("  export BOOKSHELF_TOKEN=<your_token>")
        return
    status_emoji = {"authorized": "✅", "invalid_token": "❌", "insufficient_scope": "⚠️", "error": "❌"}.get(result["status"], "?")
    print(f"{status_emoji} 授权状态: {result['status']}")
    print(f"  {result['message']}")
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

    从 BOOKSHELF_TOKEN 环境变量读取 Token，不打印 Token 明文。
    """
    result = collect_auth_status()
    emit_auth_status(result, json_output)
    if result["status"] != "authorized":
        raise typer.Exit(code=1)
