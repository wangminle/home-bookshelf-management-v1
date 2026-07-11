"""BUG-047/048: 初始化绑定与渠道身份唯一性。"""


def test_create_member_then_first_anonymous_bind_ok(client):
    """README 流程：先 POST /members，再匿名 bind 应成功（系统尚无任何绑定）。"""
    m = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    assert m.status_code == 201
    member_id = m.json()["data"]["id"]

    bind = client.post(
        "/api/v1/members/bind",
        json={
            "member_id": member_id,
            "channel": "feishu",
            "external_user_id": "ou_owner",
        },
    )
    assert bind.status_code == 200, bind.text
    assert bind.json()["data"]["channel_bindings"]["feishu"] == "ou_owner"


def test_duplicate_channel_identity_rejected(client):
    """同一 (channel, external_user_id) 不可绑定到多个成员。"""
    m1 = client.post("/api/v1/members", json={"name": "甲", "role": "owner"})
    m2 = client.post("/api/v1/members", json={"name": "乙", "role": "member"})
    id1, id2 = m1.json()["data"]["id"], m2.json()["data"]["id"]

    # 首次绑定：系统无绑定，匿名可绑
    assert (
        client.post(
            "/api/v1/members/bind",
            json={"member_id": id1, "channel": "feishu", "external_user_id": "ou_shared"},
        ).status_code
        == 200
    )

    # owner 代绑同一外部身份到另一成员 → 冲突
    dup = client.post(
        "/api/v1/members/bind",
        json={"member_id": id2, "channel": "feishu", "external_user_id": "ou_shared"},
        headers={"X-Channel": "feishu", "X-External-User-Id": "ou_shared"},
    )
    assert dup.status_code == 409, dup.text
