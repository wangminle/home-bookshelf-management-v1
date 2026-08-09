"""TST-001: 附件实体校验——不存在 book/member/note/copy→400；link 无 url→400。

附件端点需渠道鉴权，先用匿名首次 bind 建立绑定，再带渠道头请求。
"""


def _setup_owner_and_book(client):
    m = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    member_id = m.json()["data"]["id"]
    client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_test"},
    )
    headers = {"X-Channel": "feishu", "X-External-User-Id": "ou_test"}
    # BUG-113：白名单建立后创建书籍也需渠道头
    book = client.post("/api/v1/books", json={"title": "附件测试书"}, headers=headers)
    book_id = book.json()["data"]["id"]
    return member_id, book_id, headers


def _post_attachment(client, headers, **fields):
    return client.post("/api/v1/attachments", data=fields, headers=headers)


def test_attachment_unknown_book_400(client):
    _, _, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="book",
        entity_id="9999",
        attach_type="markdown",
        content_md="内容",
    )
    assert r.status_code == 400, r.text


def test_attachment_unknown_member_400(client):
    _, _, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="member",
        entity_id="9999",
        attach_type="markdown",
        content_md="内容",
    )
    assert r.status_code == 400, r.text


def test_attachment_unknown_note_400(client):
    _, _, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="note",
        entity_id="9999",
        attach_type="markdown",
        content_md="内容",
    )
    assert r.status_code == 400, r.text


def test_attachment_unknown_copy_400(client):
    _, _, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="copy",
        entity_id="9999",
        attach_type="markdown",
        content_md="内容",
    )
    assert r.status_code == 400, r.text


def test_attachment_link_without_url_400(client):
    _, book_id, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="book",
        entity_id=str(book_id),
        attach_type="link",
    )
    assert r.status_code == 400, r.text


def test_attachment_markdown_without_content_400(client):
    _, book_id, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="book",
        entity_id=str(book_id),
        attach_type="markdown",
    )
    assert r.status_code == 400, r.text


def test_attachment_valid_markdown_201(client):
    _, book_id, headers = _setup_owner_and_book(client)
    r = _post_attachment(
        client,
        headers,
        entity_type="book",
        entity_id=str(book_id),
        attach_type="markdown",
        content_md="这是一段笔记",
    )
    assert r.status_code == 201, r.text
    assert r.json()["data"]["content_md"] == "这是一段笔记"
