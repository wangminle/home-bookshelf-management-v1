from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import List, Optional

import typer

from bookshelf.client import BookshelfClient, emit
from bookshelf.doctor import emit_doctor, run_doctor
from bookshelf.bootstrap import cmd_auth_login, cmd_auth_logout, cmd_auth_status, cmd_bootstrap
from bookshelf.credentials import TokenFileError, resolve_token
from bookshelf.isbn import normalize_isbn
from bookshelf.location import summarize_locations

app = typer.Typer(help="家庭图书管理系统 CLI", no_args_is_help=True)
client = BookshelfClient()


def clean_cli_errors(func):
    """业务错误不再以 Python traceback 抛给用户。

    client 层把连接失败/HTTP 4xx 5xx/业务错误统一抛 RuntimeError；
    此前未捕获会整段 traceback 直接面向使用者。这里转为一行错误：
    JSON 模式输出 {"ok": false, "error": ...}（保持 stdout 可解析），
    文本模式输出到 stderr，均以 exit code 1 退出。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        json_output = kwargs.get("json_output", True)
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            # 子命令已输出完整失败文档，不能把主动退出再包装成第二段 JSON。
            raise
        except RuntimeError as exc:
            if json_output:
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            else:
                typer.secho(f"❌ {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
    return wrapper


def _expand_image(path: Path | None, option_name: str) -> Path | None:
    """展开 ~ 前缀并校验文件存在（typer 的 exists=True 不会做 expanduser）。"""
    if path is None:
        return None
    expanded = path.expanduser()
    if not expanded.is_file():
        raise typer.BadParameter(f"图片不存在：{expanded}", param_hint=option_name)
    return expanded


@app.command("add")
@clean_cli_errors
def add_book(
    isbn: Optional[str] = typer.Option(None, "--isbn", help="ISBN-10/13"),
    title: Optional[str] = typer.Option(None, "--title", help="书名"),
    author: Optional[str] = typer.Option(None, "--author", help="作者"),
    authors: Optional[List[str]] = typer.Option(None, "--authors", help="作者数组（可重复传入，多作者完整保留）"),
    image: Optional[Path] = typer.Option(None, "--image", dir_okay=False, help="书封/条码图片（支持 ~ 路径）"),
    price: Optional[float] = typer.Option(None, "--price", help="购买价格"),
    channel: Optional[str] = typer.Option(None, "--channel", help="购买渠道"),
    location: Optional[str] = typer.Option(None, "--location", help="存放位置"),
    member_id: Optional[int] = typer.Option(None, "--member-id", help="家庭成员 ID"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """入库：支持 ISBN / 图片 / 书名+作者（--authors 传多作者数组）"""
    result = client.add(
        isbn=isbn,
        title=title,
        author=author,
        authors=authors,
        image=_expand_image(image, "--image"),
        price=price,
        channel=channel,
        location=location,
        member_id=member_id,
    )
    emit(result, json_output)


@app.command("find")
@clean_cli_errors
def find_books(
    keyword: Optional[str] = typer.Option(None, "--keyword", help="关键词（书名）"),
    author: Optional[str] = typer.Option(None, "--author", help="作者"),
    isbn: Optional[str] = typer.Option(None, "--isbn", help="ISBN"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """搜索藏书"""
    result = client.find(keyword=keyword, author=author, isbn=isbn)
    emit(result, json_output)


@app.command("show")
@clean_cli_errors
def show_book(
    book_id: int = typer.Option(..., "--id", help="书籍 ID"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """查看书籍详情"""
    result = client.show(book_id)
    emit(result, json_output)


@app.command("locate")
@clean_cli_errors
def locate_book(
    book_id: int = typer.Option(..., "--id", help="书籍 ID"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """只读定位：这本书的每个副本归属哪里、是否在架

    结构化位置需要 locations:read；没有该 Scope、没有副本或没有位置时
    如实说明，不作推断；外借等状态只显示归属位置并注明当前不在架。
    """
    result = client.show(book_id)
    data = result.get("data", result) if isinstance(result, dict) else {}
    summary = summarize_locations(data if isinstance(data, dict) else {})
    if json_output:
        print(json.dumps({"ok": True, "data": summary}, ensure_ascii=False, indent=2))
    else:
        print(summary["summary"])


@app.command("set-isbn")
@clean_cli_errors
def set_isbn(
    book_id: int = typer.Option(..., "--id", help="书籍 ID"),
    isbn: str = typer.Option(..., "--isbn", help="要补的 ISBN-10/13（校验位须正确）"),
    replace: bool = typer.Option(False, "--replace", help="书已有不同 ISBN 时允许覆盖（默认拒绝）"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """给已有书补 ISBN（需 books:write）。默认只补缺失，不覆盖已有的不同 ISBN"""
    normalized = normalize_isbn(isbn)
    if normalized is None:
        raise RuntimeError(f"ISBN 校验位不正确或格式非法：{isbn}")
    current = (client.show(book_id).get("data") or {})
    existing = (
        normalize_isbn(current.get("isbn13"))
        or normalize_isbn(current.get("isbn10"))
        or current.get("isbn13")
        or current.get("isbn10")
    )
    if existing and existing == normalized:
        data = {"id": book_id, "action": "unchanged", "isbn13": normalized, "message": "书已是该 ISBN，无需修改"}
    else:
        if existing and not replace:
            raise RuntimeError(
                f"书 {book_id} 已有 ISBN {existing}，与 {normalized} 不同；"
                f"确认要覆盖请加 --replace（建议先核对两本是否真是同一本书）")
        client.update_book_isbn(book_id, normalized, expected_isbn13=current.get("isbn13"))
        data = {"id": book_id, "action": "replaced" if existing else "set", "isbn13": normalized,
                "previous_isbn13": existing or None}
    if json_output:
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
    else:
        print(data.get("message") or f"书 {book_id} ISBN → {normalized}")


def _book_brief(resp: dict) -> dict:
    data = resp.get("data") or {}
    return {"id": data.get("id"), "title": data.get("title"), "isbn13": data.get("isbn13"),
            "copies": len(data.get("copies") or [])}


@app.command("merge")
@clean_cli_errors
def merge_books_cmd(
    target: int = typer.Option(..., "--target", help="保留的书 ID"),
    source: int = typer.Option(..., "--source", help="被合并进来、合并后删除的书 ID"),
    yes: bool = typer.Option(False, "--yes", help="确认执行（默认只预览；需 books:delete，不可逆）"),
    force: bool = typer.Option(False, "--force", help="两书 ISBN 不同时仍强制合并"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """合并两条重复记录：source 的副本/购买/进度/笔记等迁入 target，然后删除 source。默认只预览"""
    if target == source:
        raise RuntimeError("target 与 source 不能是同一本书")
    t = _book_brief(client.show(target))
    s = _book_brief(client.show(source))
    ti, si = normalize_isbn(t["isbn13"]), normalize_isbn(s["isbn13"])
    if ti and si and ti != si and not force:
        raise RuntimeError(
            f"两本书 ISBN 不同（{ti} vs {si}），通常不是同一本书；确认要合并请加 --force")
    data: dict = {"executed": False, "target": t, "source": s}
    if not yes:
        data["hint"] = (f"预览：将把书 {source}《{s['title']}》迁入书 {target}《{t['title']}》并删除书 {source}（不可逆）；"
                        f"确认请加 --yes")
    else:
        data["result"] = (client.merge_books(target, source, force=force,
            expected_target_isbn13=t["isbn13"] or "", expected_source_isbn13=s["isbn13"] or "").get("data") or {})
        data["executed"] = True
    if json_output:
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=2))
    else:
        print(data.get("hint") or (data["result"].get("message") or "已合并"))


storage_app = typer.Typer(help="只读位置查询（需 locations:read；不提供任何写入）", no_args_is_help=True)
app.add_typer(storage_app, name="storage")


def _scope_hint(func):
    """403 时补充缺少 locations:read 的明确提示（仍按 clean_cli_errors 输出）。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RuntimeError as exc:
            if "[HTTP 403]" in str(exc):
                raise RuntimeError(f"{exc}（读取位置需要 Owner 授予 locations:read；照片另需 files:read）") from exc
            raise

    return wrapper


@storage_app.command("rooms")
@clean_cli_errors
@_scope_hint
def storage_rooms(
    include_archived: bool = typer.Option(False, "--include-archived", help="包含已归档"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    offset: int = typer.Option(0, "--offset", min=0),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """房间列表"""
    emit(client.storage_rooms(include_archived=include_archived, limit=limit, offset=offset), json_output)


@storage_app.command("shelves")
@clean_cli_errors
@_scope_hint
def storage_shelves(
    room_id: Optional[int] = typer.Option(None, "--room-id", help="按房间过滤"),
    include_archived: bool = typer.Option(False, "--include-archived", help="包含已归档"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    offset: int = typer.Option(0, "--offset", min=0),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """书架列表"""
    emit(client.storage_shelves(room_id=room_id, include_archived=include_archived, limit=limit, offset=offset), json_output)


@storage_app.command("shelf")
@clean_cli_errors
@_scope_hint
def storage_shelf(
    shelf_id: int = typer.Option(..., "--id", help="书架 ID"),
    include_archived: bool = typer.Option(False, "--include-archived", help="包含已归档层/格"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """书架详情：层格结构与统计"""
    emit(client.storage_shelf(shelf_id, include_archived=include_archived), json_output)


@storage_app.command("cell")
@clean_cli_errors
@_scope_hint
def storage_cell(
    cell_id: int = typer.Option(..., "--id", help="格子 ID"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    offset: int = typer.Option(0, "--offset", min=0),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """某个格子里的副本清单"""
    emit(client.storage_cell_copies(cell_id, limit=limit, offset=offset), json_output)


@storage_app.command("unlocated")
@clean_cli_errors
@_scope_hint
def storage_unlocated(
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    offset: int = typer.Option(0, "--offset", min=0),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """三类未定位清单：有实体副本无位置 / 仅旧文字位置 / 书目无实体副本"""
    emit(client.storage_unlocated(limit=limit, offset=offset), json_output)


@app.command("recognize")
@clean_cli_errors
def recognize_isbn(
    image: Path = typer.Option(..., "--image", dir_okay=False, help="条码/书封图片（支持 ~ 路径）"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """识别图片中的 ISBN 条码"""
    result = client.recognize_isbn(_expand_image(image, "--image"))
    emit(result, json_output)


@app.command("progress")
@clean_cli_errors
def update_progress(
    book_id: int = typer.Option(..., "--book-id", help="书籍 ID"),
    member_id: Optional[int] = typer.Option(None, "--member-id", help="家庭成员 ID"),
    status: Optional[str] = typer.Option(None, "--status", help="unread / reading / finished / abandoned / dropped"),
    page: Optional[int] = typer.Option(None, "--page", help="当前页码"),
    percent: Optional[float] = typer.Option(None, "--percent", help="阅读进度百分比 0-100"),
    rating: Optional[int] = typer.Option(None, "--rating", help="评分 1-5"),
    to_read: Optional[bool] = typer.Option(None, "--to-read/--no-to-read", help="标记/取消想读"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """更新阅读进度"""
    result = client.progress(
        book_id=book_id,
        member_id=member_id,
        status=status,
        page=page,
        percent=percent,
        rating=rating,
        to_read=to_read,
    )
    emit(result, json_output)


@app.command("purchase")
@clean_cli_errors
def log_purchase(
    book_id: int = typer.Option(..., "--book-id", help="书籍 ID"),
    price: float = typer.Option(..., "--price", help="购买价格"),
    original_price: Optional[float] = typer.Option(None, "--original-price", help="定价（用于折扣统计）"),
    channel: Optional[str] = typer.Option(None, "--channel", help="购买渠道"),
    order_no: Optional[str] = typer.Option(None, "--order-no", help="订单号"),
    purchase_date: Optional[str] = typer.Option(None, "--date", help="购买日期 YYYY-MM-DD"),
    member_id: Optional[int] = typer.Option(None, "--member-id", help="购买者成员 ID"),
    notes: Optional[str] = typer.Option(None, "--notes", help="备注"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """记录购买信息"""
    result = client.purchase(
        book_id=book_id,
        price=price,
        original_price=original_price,
        channel=channel,
        order_no=order_no,
        purchase_date=purchase_date,
        member_id=member_id,
        notes=notes,
    )
    emit(result, json_output)


@app.command("health")
@clean_cli_errors
def health(json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出")):
    """检查 API 服务状态"""
    result = client.health()
    emit(result, json_output)


@app.command("note")
@clean_cli_errors
def add_note(
    book_id: int = typer.Option(..., "--book-id", help="书籍 ID"),
    content: str = typer.Option(..., "--content", help="笔记内容（Markdown）"),
    member_id: int | None = typer.Option(None, "--member-id", help="家庭成员 ID"),
    note_type: str = typer.Option("excerpt", "--type", help="excerpt / review / thought"),
    page: int | None = typer.Option(None, "--page", help="页码"),
    chapter: str | None = typer.Option(None, "--chapter", help="章节"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """添加读书笔记"""
    result = client.note(
        book_id=book_id,
        content=content,
        member_id=member_id,
        note_type=note_type,
        page=page,
        chapter=chapter,
    )
    emit(result, json_output)


@app.command("reading-log")
@clean_cli_errors
def add_reading_log(
    book_id: int = typer.Option(..., "--book-id", help="书籍 ID"),
    log_date: str = typer.Option(..., "--date", help="日期 YYYY-MM-DD"),
    pages: int = typer.Option(0, "--pages", help="当日阅读页数"),
    minutes: int | None = typer.Option(None, "--minutes", help="阅读分钟数"),
    member_id: int | None = typer.Option(None, "--member-id", help="家庭成员 ID"),
    notes: str | None = typer.Option(None, "--notes", help="备注"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """记录每日阅读日志"""
    result = client.reading_log(
        book_id=book_id,
        log_date=log_date,
        pages_read=pages,
        minutes_read=minutes,
        member_id=member_id,
        notes=notes,
    )
    emit(result, json_output)


@app.command("stats")
@clean_cli_errors
def show_stats(json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出")):
    """查看藏书与阅读统计"""
    result = client.stats()
    emit(result, json_output)


@app.command("doctor")
def doctor(
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
    authorized: bool = typer.Option(False, "--authorized", help="授权后业务检查（需要 BOOKSHELF_TOKEN）"),
):
    """初始化诊断：检查 API、数据库、Key、成员绑定等

    默认只做公开发现面检查（无需认证）。
    加 --authorized 做业务连通性检查（需要 BOOKSHELF_TOKEN）。
    """
    if authorized:
        # WBS-8：授权后业务检查
        from bookshelf.bootstrap import collect_auth_status, emit_auth_status
        try:
            token = resolve_token()
        except TokenFileError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            raise typer.Exit(code=1)
        if token is None:
            print(json.dumps({
                "ok": False,
                "error": "未设置 BOOKSHELF_TOKEN / BOOKSHELF_TOKEN_FILE，也无默认 Token 文件，"
                         "无法执行授权后检查（可运行 bookshelf auth login）",
            }, ensure_ascii=False))
            raise typer.Exit(code=1)
        # auth 失败（无效 Token/连接失败）直接以单一 JSON 退出，不再继续 doctor
        auth_result = collect_auth_status()
        if auth_result["status"] != "authorized":
            print(json.dumps({"ok": False, "auth_status": auth_result}, ensure_ascii=False, indent=2))
            raise typer.Exit(code=1)
        if not json_output:
            # 文本模式可分段输出；JSON 模式必须合并为单一文档（见下方）
            emit_auth_status(auth_result, json_output)
    payload = run_doctor(client).to_payload()
    if authorized:
        # 此前 JSON 模式下 auth status 与 doctor 各 print 一份，
        # stdout 拼成两个 JSON 文档无法解析；合并为单一文档输出
        payload["auth_status"] = auth_result
    emit_doctor(payload, json_output)
    if not payload.get("ok"):
        raise typer.Exit(code=1)


@app.command("bind")
@clean_cli_errors
def bind_member(
    member_id: int = typer.Option(..., "--member-id", help="家庭成员 ID"),
    channel: str = typer.Option(..., "--channel", help="渠道名，如 feishu / telegram"),
    external_user_id: str = typer.Option(..., "--external-user-id", help="渠道侧用户 ID"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """绑定 IM 渠道账号到家庭成员（白名单）"""
    result = client.bind_member(
        member_id=member_id,
        channel=channel,
        external_user_id=external_user_id,
    )
    emit(result, json_output)


@app.command("member")
@clean_cli_errors
def add_member(
    name: str = typer.Option(..., "--name", help="成员名称"),
    role: str = typer.Option("member", "--role", help="角色：owner / member"),
    avatar: Optional[str] = typer.Option(None, "--avatar", help="头像路径（可选）"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """新建家庭成员"""
    result = client.add_member(name=name, role=role, avatar_path=avatar)
    emit(result, json_output)


@app.command("bootstrap")
def bootstrap(
    url: str = typer.Argument(..., help="服务端地址，如 http://127.0.0.1:8000"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """WBS-8：发现系统契约（无需认证）"""
    cmd_bootstrap(url, json_output)


auth_app = typer.Typer(help="Agent 授权管理")
app.add_typer(auth_app, name="auth")


@auth_app.command("status")
def auth_status(
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """检查当前 Agent 授权状态"""
    cmd_auth_status(json_output)


@auth_app.command("login")
@clean_cli_errors
def auth_login(
    use_stdin: bool = typer.Option(False, "--stdin", help="从标准输入读取 Token（否则隐藏提示输入）"),
    file: Optional[Path] = typer.Option(None, "--file", help="Token 文件路径，保存后记住供后续命令使用（环境变量仍优先）"),
    verify: bool = typer.Option(True, "--verify/--no-verify", help="保存前用 /auth/introspect 校验 Token"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """保存 Agent Token 到 0600 文件，避免 Token 出现在命令行和进程列表

    没有 --token 参数：Token 仅来自标准输入或隐藏提示。
    """
    cmd_auth_login(use_stdin, file, verify, json_output)


@auth_app.command("logout")
@clean_cli_errors
def auth_logout(
    file: Optional[Path] = typer.Option(None, "--file", help="Token 文件路径（默认同 login）"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """删除已保存的 Token 文件（幂等）"""
    cmd_auth_logout(file, json_output)


if __name__ == "__main__":
    app()
