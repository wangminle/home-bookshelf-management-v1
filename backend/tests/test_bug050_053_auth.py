"""BUG-050: attachments 须接入渠道鉴权；BUG-053: 不存在的 member_id 应 400 而非 500。"""


def _seed_book_and_owner(client):
    m = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    assert m.status_code == 201
    member_id = m.json()["data"]["id"]
    book = client.post("/api/v1/books", json={"title": "鉴权附件书"})
    assert book.status_code == 201
    return member_id, book.json()["data"]["id"]


def test_bug050_attachment_unbound_channel_forbidden(client):
    member_id, book_id = _seed_book_and_owner(client)
    assert (
        client.post(
            "/api/v1/members/bind",
            json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_owner"},
        ).status_code
        == 200
    )

    r = client.post(
        "/api/v1/attachments",
        data={
            "entity_type": "book",
            "entity_id": str(book_id),
            "attach_type": "link",
            "title": "外链",
            "url": "https://example.com/x",
        },
        headers={"X-Channel": "feishu", "X-External-User-Id": "attacker_ou"},
    )
    assert r.status_code == 403, r.text


def test_bug050_attachment_partial_headers_rejected(client):
    _, book_id = _seed_book_and_owner(client)
    r = client.post(
        "/api/v1/attachments",
        data={
            "entity_type": "book",
            "entity_id": str(book_id),
            "attach_type": "markdown",
            "title": "备注",
            "content_md": "x",
        },
        headers={"X-Channel": "feishu"},
    )
    assert r.status_code == 400, r.text


def test_bug053_progress_unknown_member_is_400(client):
    _, book_id = _seed_book_and_owner(client)
    r = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"member_id": 99999, "status": "reading"},
    )
    assert r.status_code == 400, r.text


def test_bug053_notes_unknown_member_is_400(client):
    _, book_id = _seed_book_and_owner(client)
    r = client.post(
        f"/api/v1/books/{book_id}/notes",
        json={"member_id": 99999, "content_md": "摘录", "note_type": "excerpt"},
    )
    assert r.status_code == 400, r.text


def test_bug053_purchase_unknown_member_is_400(client):
    _, book_id = _seed_book_and_owner(client)
    r = client.post(
        f"/api/v1/books/{book_id}/purchases",
        json={"member_id": 99999, "price": 32.5, "channel": "jd"},
    )
    assert r.status_code == 400, r.text


def test_bug053_reading_log_unknown_member_is_400(client):
    _, book_id = _seed_book_and_owner(client)
    r = client.post(
        f"/api/v1/books/{book_id}/reading-logs",
        json={"member_id": 99999, "log_date": "2026-01-01", "minutes_read": 30},
    )
    assert r.status_code == 400, r.text


def test_bug053_intake_json_unknown_member_is_400(client):
    r = client.post(
        "/api/v1/books/intake/json",
        json={"title": "无效成员导入", "member_id": 99999},
    )
    assert r.status_code == 400, r.text
