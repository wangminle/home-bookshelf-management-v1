import json
import os
from pathlib import Path
from typing import Any

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = httpx.Timeout(5.0, read=30.0)
INTAKE_TIMEOUT = httpx.Timeout(5.0, read=90.0)


class BookshelfClient:
    def __init__(self, base_url: str | None = None, timeout: httpx.Timeout | float | None = None):
        self.base_url = (base_url or os.environ.get("BOOKSHELF_API_URL", DEFAULT_API_URL)).rstrip("/")
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        setup_token = os.environ.get("BOOKSHELF_SETUP_TOKEN") or os.environ.get("SETUP_TOKEN")
        if setup_token:
            headers["X-Setup-Token"] = setup_token
        x_channel = os.environ.get("BOOKSHELF_CHANNEL")
        x_external = os.environ.get("BOOKSHELF_EXTERNAL_USER_ID")
        if x_channel:
            headers["X-Channel"] = x_channel
        if x_external:
            headers["X-External-User-Id"] = x_external
        return headers

    def _request(self, method: str, path: str, *, timeout: httpx.Timeout | float | None = None, **kwargs) -> dict[str, Any]:
        request_timeout = timeout if timeout is not None else self.timeout
        headers = dict(kwargs.pop("headers", None) or {})
        for key, value in self._auth_headers().items():
            headers.setdefault(key, value)
        try:
            with httpx.Client(timeout=request_timeout) as client:
                resp = client.request(method, self._url(path), headers=headers or None, **kwargs)
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"连接 API 超时：{self.base_url}（{method} {path}）") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"无法连接 API：{self.base_url}（{exc.__class__.__name__}）") from exc

        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(f"API 返回非 JSON（{resp.status_code}）: {resp.text[:200]}") from exc

        if resp.status_code >= 400:
            if isinstance(payload, dict):
                detail = payload.get("detail")
            else:
                detail = payload
            if isinstance(detail, list):
                if detail:
                    first = detail[0]
                    if isinstance(first, dict):
                        detail = first.get("msg", str(detail))
                    else:
                        detail = str(first)
                else:
                    detail = str(detail)
            prefix = f"[HTTP {resp.status_code}] "
            raise RuntimeError(prefix + str(detail or resp.text))

        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError(payload.get("error") or "API 请求失败")

        if isinstance(payload, dict):
            payload["_http_status"] = resp.status_code
        return payload

    def health_probe(self) -> tuple[dict[str, Any] | None, int | None]:
        request_timeout = self.timeout
        try:
            with httpx.Client(timeout=request_timeout) as client:
                resp = client.get(self._url("/health"))
                try:
                    payload = resp.json()
                except Exception:
                    return None, resp.status_code
                if isinstance(payload, dict):
                    payload["_http_status"] = resp.status_code
                return payload, resp.status_code
        except httpx.HTTPError:
            return None, None

    def health(self) -> dict[str, Any]:
        payload, status = self.health_probe()
        if payload is None:
            raise RuntimeError(f"无法连接 API：{self.base_url}")
        if status and status >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else payload
            raise RuntimeError(f"[HTTP {status}] {detail or 'health 检查失败'}")
        return payload

    def members(self) -> dict[str, Any]:
        return self._request("GET", "/members")

    def add_member(self, *, name: str, role: str = "member", avatar_path: str | None = None) -> dict[str, Any]:
        body = {k: v for k, v in {
            "name": name,
            "role": role,
            "avatar_path": avatar_path,
        }.items() if v is not None}
        return self._request("POST", "/members", json=body)

    def bind_member(self, *, member_id: int, channel: str, external_user_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/members/bind",
            json={
                "member_id": member_id,
                "channel": channel,
                "external_user_id": external_user_id,
            },
        )

    def find(self, keyword: str | None = None, author: str | None = None, isbn: str | None = None) -> dict[str, Any]:
        params = {k: v for k, v in {"keyword": keyword, "author": author, "isbn": isbn}.items() if v}
        return self._request("GET", "/books", params=params)

    def show(self, book_id: int) -> dict[str, Any]:
        return self._request("GET", f"/books/{book_id}")

    def add(
        self,
        *,
        isbn: str | None = None,
        title: str | None = None,
        author: str | None = None,
        image: Path | None = None,
        price: float | None = None,
        channel: str | None = None,
        location: str | None = None,
        member_id: int | None = None,
    ) -> dict[str, Any]:
        if image:
            with image.open("rb") as f:
                files = {"image": (image.name, f, "application/octet-stream")}
                data = {k: str(v) for k, v in {
                    "isbn": isbn, "title": title, "author": author,
                    "price": price, "channel": channel, "location": location,
                    "member_id": member_id,
                }.items() if v is not None}
                return self._request("POST", "/books/intake", data=data, files=files, timeout=INTAKE_TIMEOUT)

        body = {k: v for k, v in {
            "isbn": isbn, "title": title, "author": author,
            "price": price, "channel": channel, "location": location,
            "member_id": member_id,
        }.items() if v is not None}
        return self._request("POST", "/books/intake/json", json=body, timeout=INTAKE_TIMEOUT)

    def recognize_isbn(self, image: Path) -> dict[str, Any]:
        with image.open("rb") as f:
            files = {"image": (image.name, f, "application/octet-stream")}
            return self._request("POST", "/recognize/isbn", files=files)

    def progress(
        self,
        *,
        book_id: int,
        member_id: int | None = None,
        status: str | None = None,
        page: int | None = None,
        percent: float | None = None,
        rating: int | None = None,
    ) -> dict[str, Any]:
        body = {k: v for k, v in {
            "member_id": member_id,
            "status": status,
            "current_page": page,
            "percent": percent,
            "rating": rating,
        }.items() if v is not None}
        return self._request("POST", f"/books/{book_id}/progress", json=body)

    def purchase(
        self,
        *,
        book_id: int,
        price: float,
        original_price: float | None = None,
        channel: str | None = None,
        order_no: str | None = None,
        purchase_date: str | None = None,
        member_id: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        body = {k: v for k, v in {
            "price": price,
            "original_price": original_price,
            "channel": channel,
            "order_no": order_no,
            "purchase_date": purchase_date,
            "member_id": member_id,
            "notes": notes,
        }.items() if v is not None}
        return self._request("POST", f"/books/{book_id}/purchases", json=body)

    def note(
        self,
        *,
        book_id: int,
        content: str,
        member_id: int | None = None,
        note_type: str = "excerpt",
        page: int | None = None,
        chapter: str | None = None,
    ) -> dict[str, Any]:
        body = {k: v for k, v in {
            "member_id": member_id,
            "note_type": note_type,
            "content_md": content,
            "page": page,
            "chapter": chapter,
        }.items() if v is not None}
        return self._request("POST", f"/books/{book_id}/notes", json=body)

    def reading_log(
        self,
        *,
        book_id: int,
        log_date: str,
        pages_read: int = 0,
        minutes_read: int | None = None,
        member_id: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        body = {k: v for k, v in {
            "member_id": member_id,
            "log_date": log_date,
            "pages_read": pages_read,
            "minutes_read": minutes_read,
            "notes": notes,
        }.items() if v is not None}
        return self._request("POST", f"/books/{book_id}/reading-logs", json=body)

    def stats(self) -> dict[str, Any]:
        return self._request("GET", "/stats")


def emit(payload: dict[str, Any], as_json: bool) -> None:
    clean = {k: v for k, v in payload.items() if k != "_http_status"} if isinstance(payload, dict) else payload
    if as_json:
        print(json.dumps(clean, ensure_ascii=False, indent=2, default=str))
        return

    data = clean.get("data", clean) if isinstance(clean, dict) else clean
    if isinstance(data, dict) and "message" in data:
        print(data["message"])
        book = data.get("book")
        if book:
            authors = ", ".join(book.get("authors") or []) or "未知作者"
            print(f"  ID: {book.get('id')}  《{book.get('title')}》  {authors}")
            if book.get("isbn13"):
                print(f"  ISBN: {book.get('isbn13')}")
        if data.get("status"):
            extra = []
            if data.get("current_page") is not None:
                extra.append(f"第 {data['current_page']} 页")
            if data.get("percent") is not None:
                extra.append(f"{data['percent']}%")
            if data.get("rating") is not None:
                extra.append(f"评分 {data['rating']}")
            if extra:
                print(f"  进度: {', '.join(extra)}")
        if data.get("price") is not None:
            currency = data.get("currency") or "CNY"
            symbol = "¥" if currency == "CNY" else f"{currency} "
            print(f"  价格: {symbol}{data['price']}")
        return

    if isinstance(data, dict) and "items" in data:
        items = data["items"]
        total = data.get("total", len(items))
        label = "本" if items and isinstance(items[0], dict) and "title" in items[0] else "条"
        print(f"共 {total} {label}，显示 {len(items)} {label}：")
        for item in items:
            if not isinstance(item, dict):
                print(f"  {item}")
                continue
            if "title" in item:
                authors = ", ".join(item.get("authors") or []) or "未知作者"
                print(f"  [{item.get('id')}] 《{item.get('title')}》 — {authors}")
            elif "name" in item:
                print(f"  [{item.get('id')}] {item.get('name')} ({item.get('role') or ''})")
            else:
                print(f"  {json.dumps(item, ensure_ascii=False)}")
        return

    # health / stats / show 等
    if isinstance(data, dict):
        if "database" in data or "app" in data:
            print(f"状态: {data.get('status', 'ok')}")
            if data.get("app"):
                print(f"  应用: {data['app']}")
            if data.get("database"):
                print(f"  数据库: {data['database']}")
            if "barcode_scan_available" in data:
                print(f"  条码识别: {'可用' if data['barcode_scan_available'] else '不可用'}")
            return
        if "total_books" in data:
            print(f"藏书 {data.get('total_books', 0)} 本")
            by_status = data.get("by_status") or {}
            if by_status:
                parts = [f"{k}:{v}" for k, v in by_status.items() if v]
                if parts:
                    print(f"  状态: {', '.join(parts)}")
            print(f"  花费: ¥{data.get('total_spent', 0)}（{data.get('purchase_count', 0)} 笔 CNY）")
            print(f"  阅读页数: {data.get('reading_logs_pages_total', 0)}")
            for m in data.get("members") or []:
                print(
                    f"  成员 {m.get('name')}: 在读 {m.get('books_reading', 0)} / "
                    f"读完 {m.get('books_finished', 0)} / 连续 {m.get('reading_streak', 0)} 天"
                )
            return
        if "title" in data and "id" in data:
            authors = ", ".join(data.get("authors") or []) or "未知作者"
            print(f"[{data.get('id')}] 《{data.get('title')}》 — {authors}")
            if data.get("isbn13"):
                print(f"  ISBN: {data.get('isbn13')}")
            if data.get("publisher"):
                print(f"  出版社: {data.get('publisher')}")
            if data.get("category"):
                print(f"  分类: {data.get('category')}")
            return

    print(json.dumps(clean, ensure_ascii=False, indent=2, default=str))
