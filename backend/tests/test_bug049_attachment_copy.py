"""BUG-049: 附件 API 支持 entity_type=copy。"""

from pathlib import Path


def _setup_member_and_bind(client):
    """创建成员并绑定渠道，返回渠道请求头。"""
    m = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert m.status_code == 201, m.text
    member_id = m.json()["data"]["id"]
    bind = client.post(
        "/api/v1/members/bind",
        json={"member_id": member_id, "channel": "feishu", "external_user_id": "ou_test"},
    )
    assert bind.status_code == 200, bind.text
    return {"X-Channel": "feishu", "X-External-User-Id": "ou_test"}


def test_attachment_create_accepts_copy(client, tmp_path: Path):
    headers = _setup_member_and_bind(client)
    book = client.post("/api/v1/books", json={"title": "有副本的书"}, headers=headers)
    assert book.status_code == 201
    book_id = book.json()["data"]["id"]

    copy = client.post(
        f"/api/v1/books/{book_id}/copies",
        json={"copy_type": "physical", "location": "客厅"},
        headers=headers,
    )
    assert copy.status_code in (200, 201), copy.text
    copy_id = copy.json()["data"]["id"]

    # markdown 附件无需上传文件
    r = client.post(
        "/api/v1/attachments",
        data={
            "entity_type": "copy",
            "entity_id": str(copy_id),
            "attach_type": "markdown",
            "title": "副本备注",
            "content_md": "这是副本附件",
        },
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text
    assert r.json()["data"]["entity_type"] == "copy"
    assert r.json()["data"]["entity_id"] == copy_id
