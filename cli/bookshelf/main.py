from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from bookshelf.client import BookshelfClient, emit
from bookshelf.doctor import emit_doctor, run_doctor

app = typer.Typer(help="家庭图书管理系统 CLI", no_args_is_help=True)
client = BookshelfClient()


@app.command("add")
def add_book(
    isbn: Optional[str] = typer.Option(None, "--isbn", help="ISBN-10/13"),
    title: Optional[str] = typer.Option(None, "--title", help="书名"),
    author: Optional[str] = typer.Option(None, "--author", help="作者"),
    image: Optional[Path] = typer.Option(None, "--image", exists=True, dir_okay=False, help="书封/条码图片"),
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
        image=image,
        price=price,
        channel=channel,
        location=location,
        member_id=member_id,
    )
    emit(result, json_output)


@app.command("find")
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
def show_book(
    book_id: int = typer.Option(..., "--id", help="书籍 ID"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """查看书籍详情"""
    result = client.show(book_id)
    emit(result, json_output)


@app.command("recognize")
def recognize_isbn(
    image: Path = typer.Option(..., "--image", exists=True, dir_okay=False, help="条码/书封图片"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出"),
):
    """识别图片中的 ISBN 条码"""
    result = client.recognize_isbn(image)
    emit(result, json_output)


@app.command("progress")
def update_progress(
    book_id: int = typer.Option(..., "--book-id", help="书籍 ID"),
    member_id: Optional[int] = typer.Option(None, "--member-id", help="家庭成员 ID"),
    status: Optional[str] = typer.Option(None, "--status", help="unread / reading / finished / abandoned / dropped"),
    page: Optional[int] = typer.Option(None, "--page", help="当前页码"),
    percent: Optional[float] = typer.Option(None, "--percent", help="阅读进度百分比 0-100"),
    rating: Optional[int] = typer.Option(None, "--rating", help="评分 1-5"),
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
    )
    emit(result, json_output)


@app.command("purchase")
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
def health(json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出")):
    """检查 API 服务状态"""
    result = client.health()
    emit(result, json_output)


@app.command("note")
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
def show_stats(json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出")):
    """查看藏书与阅读统计"""
    result = client.stats()
    emit(result, json_output)


@app.command("doctor")
def doctor(json_output: bool = typer.Option(True, "--json/--no-json", help="JSON 输出")):
    """初始化诊断：检查 API、数据库、Key、成员绑定等"""
    payload = run_doctor(client).to_payload()
    emit_doctor(payload, json_output)
    if not payload.get("ok"):
        raise typer.Exit(code=1)


@app.command("bind")
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


if __name__ == "__main__":
    app()
