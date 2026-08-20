"""BUG-044: 渠道白名单鉴权不可被半组头绕过；白名单建立后禁止匿名扩白。"""


def test_partial_channel_headers_rejected(client):
    # 准备一本书与成员，便于打 progress
    m = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    assert m.status_code == 201
    book = client.post("/api/v1/books", json={"title": "鉴权测试"})
    assert book.status_code == 201
    book_id = book.json()["data"]["id"]

    r = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"status": "reading"},
        headers={"X-Channel": "feishu"},
    )
    assert r.status_code == 400, r.text


def test_anonymous_bind_blocked_after_whitelist_exists(client):
    """白名单已建立后，匿名不可再自助写入新身份。"""
    m1 = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    assert m1.status_code == 201
    id1 = m1.json()["data"]["id"]

    assert (
        client.post(
            "/api/v1/members/bind",
            json={"member_id": id1, "channel": "feishu", "external_user_id": "ou_owner"},
        ).status_code
        == 200
    )

    book = client.post(
        "/api/v1/books",
        json={"title": "鉴权测试2"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_owner"},
    )
    assert book.status_code == 201
    book_id = book.json()["data"]["id"]

    m2 = client.post(
        "/api/v1/members",
        json={"name": "乙", "role": "member"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_owner"},
    )
    assert m2.status_code == 201
    id2 = m2.json()["data"]["id"]

    client.cookies.clear()  # 夹具默认 owner 会话；此处验证真正的匿名 bind
    bind = client.post(
        "/api/v1/members/bind",
        json={
            "member_id": id2,
            "channel": "feishu",
            "external_user_id": "attacker_ou",
        },
    )
    assert bind.status_code == 403, bind.text

    progress = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"status": "reading"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "attacker_ou"},
    )
    assert progress.status_code == 403, progress.text


def test_empty_db_bootstrap_bind_still_works(client):
    """空库仍允许 bind member_id=1 引导创建默认 owner（BUG-035）。"""
    bind = client.post(
        "/api/v1/members/bind",
        json={
            "member_id": 1,
            "channel": "feishu",
            "external_user_id": "ou_owner",
        },
    )
    assert bind.status_code == 200, bind.text
    assert bind.json()["data"]["id"] == 1
