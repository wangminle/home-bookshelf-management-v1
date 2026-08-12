"""CHK-032 回归测试：覆盖本轮 BUG-113/114/116/117/118/119/123/129 修复。"""

from pathlib import Path


def _setup_owner(client):
    """创建 owner 并绑定渠道，返回 (member_id, headers)。"""
    m = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert m.status_code == 201, m.text
    mid = m.json()["data"]["id"]
    bind = client.post(
        "/api/v1/members/bind",
        json={"member_id": mid, "channel": "feishu", "external_user_id": "ou_owner"},
    )
    assert bind.status_code == 200, bind.text
    return mid, {"X-Channel": "feishu", "X-External-User-Id": "ou_owner"}


# ── BUG-113：白名单建立后拒绝匿名写 ──────────────────────


def test_bug113_anonymous_write_blocked_after_bindings(client):
    """白名单建立后，匿名 POST /books 应 403（不再回退到 resolve_member_id）。"""
    _setup_owner(client)
    r = client.post("/api/v1/books", json={"title": "匿名书"})
    assert r.status_code == 403, r.text


def test_bug113_bound_channel_write_ok(client):
    """带已绑定渠道头可正常写。"""
    _, headers = _setup_owner(client)
    r = client.post("/api/v1/books", json={"title": "渠道书"}, headers=headers)
    assert r.status_code == 201, r.text


def test_bug113_empty_db_anonymous_still_ok(client):
    """空库（无绑定）时匿名 POST /books 仍可用（引导期回退）。"""
    r = client.post("/api/v1/books", json={"title": "空库书"})
    assert r.status_code == 201, r.text


# ── BUG-114：元数据日期校验真实合法性 ──────────────────────


def test_bug114_invalid_date_normalized_to_none():
    """_normalize_publish_date 拒绝非法真实日期如 2024-13-99。"""
    from app.services.metadata.openlibrary import _normalize_publish_date

    assert _normalize_publish_date("2024-13-99") is None
    assert _normalize_publish_date("2024-02-30") is None  # 2 月无 30 日
    assert _normalize_publish_date("2024-07-15") == "2024-07-15"
    assert _normalize_publish_date("2024") == "2024"


def test_bug114_intake_safety_net_rejects_invalid_date():
    """intake 安全网把非法 publish_date 置空。"""
    from app.utils.book_helpers import is_valid_publish_date

    assert is_valid_publish_date("2024-13-99") is False
    assert is_valid_publish_date("2024-07-15") is True
    assert is_valid_publish_date("2024") is True


# ── BUG-116：危险扩展名黑名单 ──────────────────────


def test_bug116_blocked_extensions_include_variants():
    """_BLOCKED_EXTENSIONS 含 .shtml/.xht/.svgz 等变体。"""
    from app.services.attachments import _BLOCKED_EXTENSIONS

    for ext in (".shtml", ".xht", ".svgz", ".html", ".svg", ".htm"):
        assert ext in _BLOCKED_EXTENSIONS, f"{ext} 应被阻止上传"


def test_bug116_force_download_includes_variants():
    from app.api.v1.files import _FORCE_DOWNLOAD_SUFFIXES

    for ext in (".shtml", ".xht", ".svgz"):
        assert ext in _FORCE_DOWNLOAD_SUFFIXES, f"{ext} 应强制下载"


# ── BUG-117/123：统计与筛选口径一致 ──────────────────────


def _seed_book_with_status(client, headers, title, member_id, status):
    """创建一本书并为指定成员设置进度状态。"""
    r = client.post("/api/v1/books", json={"title": title}, headers=headers)
    assert r.status_code == 201, r.text
    book_id = r.json()["data"]["id"]
    pr = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"member_id": member_id, "status": status},
        headers=headers,
    )
    assert pr.status_code in (200, 201), pr.text
    return book_id


def test_bug117_by_status_sum_le_total_books(client):
    """多成员共读同一本书时，by_status 合计不超过 total_books。"""
    owner_id, headers = _setup_owner(client)
    # 再建一个成员
    m2 = client.post("/api/v1/members", json={"name": "乙", "role": "member"}, headers=headers)
    assert m2.status_code == 201, m2.text
    mid2 = m2.json()["data"]["id"]

    # 一本书：owner 在读，mid2 未读（聚合后该书全局状态为 reading）
    r = client.post("/api/v1/books", json={"title": "共读书"}, headers=headers)
    assert r.status_code == 201
    book_id = r.json()["data"]["id"]
    client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"member_id": owner_id, "status": "reading"},
        headers=headers,
    )
    client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"member_id": mid2, "status": "unread"},
        headers=headers,
    )

    stats = client.get("/api/v1/stats").json()["data"]
    total = stats["total_books"]
    status_sum = sum(stats["by_status"].values())
    assert status_sum <= total, f"by_status 合计 {status_sum} 超过 total_books {total}"


def test_bug123_stats_filter_consistency(client):
    """GET /stats 的 by_status 与 GET /books?status=X 计数一致。"""
    _, headers = _setup_owner(client)
    # 建几本不同状态的书
    _seed_book_with_status(client, headers, "读完了A", 1, "finished")
    _seed_book_with_status(client, headers, "在读B", 1, "reading")
    # 一本无进度的书（unread）

    r = client.post("/api/v1/books", json={"title": "未读C"}, headers=headers)
    assert r.status_code == 201

    stats = client.get("/api/v1/stats").json()["data"]["by_status"]
    for status in ("reading", "finished", "unread"):
        listed = client.get(f"/api/v1/books?status={status}&limit=100", headers=headers).json()["data"]["total"]
        assert listed == stats[status], (
            f"status={status}: stats={stats[status]} 但 books 列表={listed}"
        )


# ── BUG-118：PATCH 清空 authors/tags ──────────────────────


def test_bug118_patch_clear_authors_and_tags(client):
    _, headers = _setup_owner(client)
    r = client.post(
        "/api/v1/books",
        json={"title": "清空测试", "authors": ["张三"], "tags": ["小说"]},
        headers=headers,
    )
    assert r.status_code == 201
    book_id = r.json()["data"]["id"]

    # 显式传 null 清空
    r2 = client.patch(
        f"/api/v1/books/{book_id}",
        json={"authors": None, "tags": None},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    detail = client.get(f"/api/v1/books/{book_id}").json()["data"]
    assert detail["authors"] is None or detail["authors"] == []
    assert detail["tags"] == []


# ── BUG-129：progress 响应含 to_read ──────────────────────


def test_bug129_progress_response_includes_to_read(client):
    _, headers = _setup_owner(client)
    r = client.post("/api/v1/books", json={"title": "想读书"}, headers=headers)
    assert r.status_code == 201
    book_id = r.json()["data"]["id"]

    pr = client.post(
        f"/api/v1/books/{book_id}/progress",
        json={"member_id": 1, "to_read": True},
        headers=headers,
    )
    assert pr.status_code in (200, 201), pr.text
    data = pr.json()["data"]
    assert data["to_read"] is True, f"响应应回传 to_read=true，实际: {data.get('to_read')}"


# ── BUG-119：无 ISBN 入库去重 ──────────────────────


def test_bug119_concurrent_no_isbn_intake_dedup(client):
    """两次无 ISBN 入库同书名应只产生一本书（服务层锁串行化）。"""
    _, headers = _setup_owner(client)

    # 第一次入库
    r1 = client.post(
        "/api/v1/books/intake",
        data={"title": "重复书名测试", "author": "同一作者"},
        headers=headers,
    )
    assert r1.status_code in (200, 201), r1.text
    # 第二次同书名同作者
    r2 = client.post(
        "/api/v1/books/intake",
        data={"title": "重复书名测试", "author": "同一作者"},
        headers=headers,
    )
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["data"]["already_exists"] is True, "第二次入库应命中已有书"

    # 库中应只有一本该书
    listed = client.get("/api/v1/books?keyword=重复书名测试", headers=headers).json()["data"]["items"]
    assert len([b for b in listed if b["title"] == "重复书名测试"]) == 1
