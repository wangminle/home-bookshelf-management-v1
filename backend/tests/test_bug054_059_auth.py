"""BUG-054: copies/custom-fields/members 在白名单建立后拒绝匿名写入。
BUG-059: 坏图像经 service 层转 ValueError 后路由层应返回 400 而非 500。"""

import io
from pathlib import Path


def _bootstrap(client):
    """创建成员并绑定渠道，返回渠道头。"""
    m = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert m.status_code == 201, m.text
    mid = m.json()["data"]["id"]
    bind = client.post(
        "/api/v1/members/bind",
        json={"member_id": mid, "channel": "feishu", "external_user_id": "ou_test"},
    )
    assert bind.status_code == 200, bind.text
    return {"X-Channel": "feishu", "X-External-User-Id": "ou_test"}


# ── BUG-054 ──────────────────────────────────────────────


def test_bug054_copies_anonymous_rejected(client):
    """白名单建立后，匿名 POST /copies 应返回 403。"""
    headers = _bootstrap(client)
    # BUG-113：白名单建立后创建书籍也需渠道头
    book = client.post("/api/v1/books", json={"title": "测试书"}, headers=headers)
    assert book.status_code == 201
    book_id = book.json()["data"]["id"]

    # 匿名（无渠道头）应被拒绝（BUG-168 后匿名一律 401）
    client.cookies.clear()
    r = client.post(
        f"/api/v1/books/{book_id}/copies",
        json={"copy_type": "physical", "location": "书房"},
    )
    assert r.status_code in (401, 403), r.text

    # 带渠道头正常工作
    r2 = client.post(
        f"/api/v1/books/{book_id}/copies",
        json={"copy_type": "physical", "location": "书房"},
        headers=headers,
    )
    assert r2.status_code in (200, 201), r2.text


def test_bug054_custom_fields_anonymous_rejected(client):
    """白名单建立后，匿名 POST /custom-fields 应返回 403。"""
    headers = _bootstrap(client)
    # BUG-113：白名单建立后创建书籍也需渠道头
    book = client.post("/api/v1/books", json={"title": "自定义字段测试"}, headers=headers)
    book_id = book.json()["data"]["id"]

    # 匿名应被拒绝（BUG-168 后匿名一律 401）
    client.cookies.clear()
    r = client.post(
        "/api/v1/custom-fields",
        json={
            "entity_type": "book",
            "entity_id": book_id,
            "field_key": "color",
            "field_value": "blue",
        },
    )
    assert r.status_code in (401, 403), r.text

    # 带渠道头正常工作
    r2 = client.post(
        "/api/v1/custom-fields",
        json={
            "entity_type": "book",
            "entity_id": book_id,
            "field_key": "color",
            "field_value": "blue",
        },
        headers=headers,
    )
    assert r2.status_code in (200, 201), r2.text


def test_bug054_members_anonymous_rejected_after_bindings(client):
    """白名单建立后，匿名 POST /members 应返回 403。"""
    _bootstrap(client)

    client.cookies.clear()
    r = client.post("/api/v1/members", json={"name": "新人", "role": "member"})
    assert r.status_code == 403, r.text

    # 带渠道头正常工作
    r2 = client.post(
        "/api/v1/members",
        json={"name": "新人", "role": "member"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_test"},
    )
    assert r2.status_code == 201, r2.text


def test_bug054_members_bootstrap_anonymous_ok(client):
    """空库（无任何渠道绑定）时，匿名 POST /members 仍可用于引导。"""
    r = client.post("/api/v1/members", json={"name": "首个成员", "role": "owner"})
    assert r.status_code == 201, r.text


# ── BUG-059 ──────────────────────────────────────────────


def test_bug059_recognize_isbn_bad_image_returns_400(client):
    """上传非图片内容到 /recognize/isbn 应返回 400，不应 500。"""
    bad = io.BytesIO(b"not an image at all")
    r = client.post(
        "/api/v1/recognize/isbn",
        files={"image": ("bad.jpg", bad, "image/jpeg")},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"


def test_bug059_recognize_cover_bad_image_returns_400(client):
    """上传非图片内容到 /recognize/cover 应返回 400，不应 500。"""
    bad = io.BytesIO(b"not an image at all")
    r = client.post(
        "/api/v1/recognize/cover",
        files={"image": ("bad.jpg", bad, "image/jpeg")},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
