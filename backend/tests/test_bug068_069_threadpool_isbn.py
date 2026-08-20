"""BUG-068: threadpool 写路径不应复用请求 Session；BUG-069: PATCH ISBN 需做等价冲突检查。"""

from __future__ import annotations

from sqlalchemy.orm import Session as SASession

from app.api.v1 import attachments as attachments_api
from app.api.v1 import intake as intake_api


def test_bug068_intake_threadpool_opens_own_session(client, monkeypatch):
    captured = {}

    async def _fake_run_in_threadpool(func, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        assert not any(isinstance(arg, SASession) for arg in args)
        assert not any(isinstance(value, SASession) for value in kwargs.values())
        return func(*args, **kwargs)

    monkeypatch.setattr(intake_api, "run_in_threadpool", _fake_run_in_threadpool)

    r = client.post("/api/v1/books/intake", data={"title": "线程池入库测试"})
    assert r.status_code == 201, r.text
    assert captured["kwargs"]["channel"] is None
    assert captured["kwargs"]["member_id"] >= 1


def test_bug068_attachment_threadpool_opens_own_session(client, monkeypatch):
    captured = {}
    book = client.post("/api/v1/books", json={"title": "线程池附件书"})
    assert book.status_code == 201
    book_id = book.json()["data"]["id"]

    async def _fake_run_in_threadpool(func, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        assert not any(isinstance(arg, SASession) for arg in args)
        assert not any(isinstance(value, SASession) for value in kwargs.values())
        return func(*args, **kwargs)

    monkeypatch.setattr(attachments_api, "run_in_threadpool", _fake_run_in_threadpool)

    r = client.post(
        "/api/v1/attachments",
        data={
            "entity_type": "book",
            "entity_id": str(book_id),
            "attach_type": "markdown",
            "title": "线程池附件",
            "content_md": "ok",
        },
    )
    assert r.status_code == 201, r.text
    assert captured["kwargs"]["channel"] is None


def test_bug069_patch_isbn10_backfills_isbn13(client):
    book = client.post("/api/v1/books", json={"title": "ISBN 回填书"})
    assert book.status_code == 201
    book_id = book.json()["data"]["id"]

    r = client.patch(f"/api/v1/books/{book_id}", json={"isbn10": "0306406152"})
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/v1/books/{book_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["isbn10"] == "0306406152"
    assert detail.json()["data"]["isbn13"] == "9780306406157"


def test_bug069_patch_isbn10_conflict_returns_409(client):
    first = client.post("/api/v1/books", json={"title": "已存在 ISBN", "isbn13": "9780306406157"})
    second = client.post("/api/v1/books", json={"title": "待修改图书"})
    assert first.status_code == 201
    assert second.status_code == 201

    second_id = second.json()["data"]["id"]
    r = client.patch(f"/api/v1/books/{second_id}", json={"isbn10": "0306406152"})
    assert r.status_code == 409, r.text
