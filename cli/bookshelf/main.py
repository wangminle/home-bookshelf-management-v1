from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Optional

import typer

from bookshelf.client import BookshelfClient, emit
from bookshelf.doctor import emit_doctor, run_doctor
from bookshelf.bootstrap import cmd_bootstrap, cmd_auth_status

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
    image: Optional[Path] = typer.Option(None, "--image", dir_okay=False, help="书封/条码图片（支持 ~ 路径）"),
    price: Optional[float] = typer.Option(None, "--price", help="购买价格"),
    channel: Optional[str] = typer.Option(None, "--channel", help="购买渠道"),
    location: Optional[str] = typer.Option(None, "--location", help="存放位置"),
    member_id: Optional[int] = typer.Option(None, "--member-id", help="家庭成员 ID"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """入库：支持 ISBN / 图片 / 书名+作者"""
    result = client.add(
        isbn=isbn,
        title=title,
        author=author,
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
        import os
        token = os.environ.get("BOOKSHELF_TOKEN")
        if not token:
            print(json.dumps({"ok": False, "error": "未设置 BOOKSHELF_TOKEN，无法执行授权后检查"}, ensure_ascii=False))
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


if __name__ == "__main__":
    app()
