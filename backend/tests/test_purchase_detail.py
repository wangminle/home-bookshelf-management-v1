"""TST-001: 购买详情回归——POST /purchases 成功路径、字段往返、日期默认、边界拒绝。"""

from datetime import date, timedelta

from app.utils.time_helpers import local_today_iso


def _seed_book(client):
    r = client.post("/api/v1/books", json={"title": "购买测试书"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _seed_book_and_copy(client):
    book_id = _seed_book(client)
    c = client.post(f"/api/v1/books/{book_id}/copies", json={"location": "书架A"})
    assert c.status_code == 201, c.text
    return book_id, c.json()["data"]["id"]


def test_create_purchase_returns_201_with_fields(client):
    book_id = _seed_book(client)
    r = client.post(
        f"/api/v1/books/{book_id}/purchases",
        json={
            "price": 59.0,
            "original_price": 68.0,
            "channel": "jd",
            "order_no": "JD123",
            "purchase_date": "2026-01-15",
            "currency": "CNY",
        },
    )
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["book_id"] == book_id
    assert d["price"] == 59.0
    assert d["original_price"] == 68.0
    assert d["channel"] == "jd"
    assert d["order_no"] == "JD123"
    assert d["purchase_date"] == "2026-01-15"
    assert d["currency"] == "CNY"
    assert "message" in d and "¥59" in d["message"]


def test_purchase_date_defaults_to_today(client):
    book_id = _seed_book(client)
    r = client.post(f"/api/v1/books/{book_id}/purchases", json={"price": 10.0})
    assert r.status_code == 201, r.text
    assert r.json()["data"]["purchase_date"] == local_today_iso()


def test_purchase_date_empty_defaults_to_today(client):
    book_id = _seed_book(client)
    r = client.post(
        f"/api/v1/books/{book_id}/purchases",
        json={"price": 10.0, "purchase_date": ""},
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["purchase_date"] == local_today_iso()


def test_purchase_date_invalid_rejected(client):
    book_id = _seed_book(client)
    r = client.post(
        f"/api/v1/books/{book_id}/purchases",
        json={"price": 10.0, "purchase_date": "2026-13-40"},
    )
    assert r.status_code == 422


def test_original_price_round_trips_in_book_detail(client):
    book_id = _seed_book(client)
    client.post(
        f"/api/v1/books/{book_id}/purchases",
        json={"price": 59.0, "original_price": 88.0, "purchase_date": "2026-03-01"},
    )
    detail = client.get(f"/api/v1/books/{book_id}")
    assert detail.status_code == 200
    records = detail.json()["data"]["purchase_records"]
    assert len(records) == 1
    assert records[0]["original_price"] == 88.0
    assert records[0]["price"] == 59.0


def test_purchase_unknown_book_returns_400(client):
    r = client.post("/api/v1/books/9999/purchases", json={"price": 10.0})
    assert r.status_code == 400


def test_purchase_price_zero_rejected(client):
    book_id = _seed_book(client)
    r = client.post(f"/api/v1/books/{book_id}/purchases", json={"price": 0})
    assert r.status_code == 422


def test_purchase_price_negative_rejected(client):
    book_id = _seed_book(client)
    r = client.post(f"/api/v1/books/{book_id}/purchases", json={"price": -5})
    assert r.status_code == 422


def test_purchase_copy_from_other_book_rejected(client, db_session):
    from app.models import BookCopy

    book_a = _seed_book(client)
    book_b = client.post("/api/v1/books", json={"title": "另一本书"}).json()["data"]["id"]
    # 直接用 ORM 给 book_b 建副本（/copies 端点需渠道鉴权，ORM 植入更直接）
    copy_b = BookCopy(book_id=book_b, location="B架")
    db_session.add(copy_b)
    db_session.commit()
    db_session.refresh(copy_b)
    r = client.post(
        f"/api/v1/books/{book_a}/purchases",
        json={"price": 10.0, "copy_id": copy_b.id},
    )
    assert r.status_code == 400


def test_purchase_currency_non_cny_message(client):
    book_id = _seed_book(client)
    r = client.post(
        f"/api/v1/books/{book_id}/purchases",
        json={"price": 12.99, "currency": "USD", "purchase_date": "2026-01-01"},
    )
    assert r.status_code == 201, r.text
    msg = r.json()["data"]["message"]
    assert "¥" not in msg
    assert "USD" in msg
