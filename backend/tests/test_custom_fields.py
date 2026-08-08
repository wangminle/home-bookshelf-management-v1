"""TST-001: 自定义字段 upsert--插入/更新、状态码、实体校验、Literal 拒绝。"""


def _setup_auth(client):
    """创建成员并绑定渠道，返回渠道请求头。"""
    m = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert m.status_code == 201, m.text
    mid = m.json()["data"]["id"]
    bind = client.post(
        "/api/v1/members/bind",
        json={"member_id": mid, "channel": "feishu", "external_user_id": "ou_test"},
    )
    assert bind.status_code == 200, bind.text
    return {"X-Channel": "feishu", "X-External-User-Id": "ou_test"}


def _seed_book(client):
    r = client.post("/api/v1/books", json={"title": "自定义字段书"})
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def test_upsert_insert_returns_201(client):
    headers = _setup_auth(client)
    book_id = _seed_book(client)
    r = client.post(
        "/api/v1/custom-fields",
        json={
            "entity_type": "book",
            "entity_id": book_id,
            "field_key": "series",
            "field_value": "哈利波特 #1",
            "value_type": "string",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["field_key"] == "series"
    assert d["field_value"] == "哈利波特 #1"
    assert "已创建" in d["message"]


def test_upsert_update_returns_200(client):
    headers = _setup_auth(client)
    book_id = _seed_book(client)
    client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "book", "entity_id": book_id, "field_key": "series", "field_value": "v1"},
        headers=headers,
    )
    r = client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "book", "entity_id": book_id, "field_key": "series", "field_value": "v2"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["field_value"] == "v2"
    assert "已更新" in d["message"]


def test_upsert_field_appears_in_book_detail(client):
    headers = _setup_auth(client)
    book_id = _seed_book(client)
    client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "book", "entity_id": book_id, "field_key": "来源", "field_value": "京东"},
        headers=headers,
    )
    detail = client.get(f"/api/v1/books/{book_id}")
    assert detail.status_code == 200
    fields = detail.json()["data"]["custom_fields"]
    assert any(f["field_key"] == "来源" and f["field_value"] == "京东" for f in fields)


def test_unknown_entity_returns_400(client):
    headers = _setup_auth(client)
    r = client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "book", "entity_id": 9999, "field_key": "k", "field_value": "v"},
        headers=headers,
    )
    assert r.status_code == 400


def test_invalid_entity_type_rejected(client):
    headers = _setup_auth(client)
    r = client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "library", "entity_id": 1, "field_key": "k", "field_value": "v"},
        headers=headers,
    )
    assert r.status_code == 422


def test_invalid_entity_id_zero_rejected(client):
    headers = _setup_auth(client)
    r = client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "book", "entity_id": 0, "field_key": "k", "field_value": "v"},
        headers=headers,
    )
    assert r.status_code == 422


def test_field_key_empty_rejected(client):
    headers = _setup_auth(client)
    book_id = _seed_book(client)
    r = client.post(
        "/api/v1/custom-fields",
        json={"entity_type": "book", "entity_id": book_id, "field_key": "", "field_value": "v"},
        headers=headers,
    )
    assert r.status_code == 422
