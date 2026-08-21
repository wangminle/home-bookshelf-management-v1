"""Public Catalog API 测试（权限阶段 1：C 模式匿名书架）。

覆盖基线 §14-阶段 1 验收：
- 匿名可信局域网可以读取 L1（回环 / TRUSTED_LAN_CIDRS / 可信代理右值法）；
- 非可信来源、来源不明和 disabled 模式不能读取 L1（自动降级）；
- 匿名响应字段白名单严格生效，不含成员、位置、阅读、笔记、购买、路径；
- 分页最大页长和限流有效。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Book, BookCopy
from app.services import rate_limit
from app.utils.book_helpers import serialize_json_list

SENTINEL_MEMBER = "SENTINEL_MEMBER_李四"
SENTINEL_LOCATION = "SENTINEL_LOC_书架B2"


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture()
def seeded(db_session: Session) -> list[Book]:
    books = [
        Book(title="公开书一", authors=serialize_json_list(["作者甲"]), category="科幻",
             language="zh", cover_path="covers/pub1.jpg"),
        Book(title="公开书二", authors=serialize_json_list(["作者乙"]), category="童话"),
    ]
    db_session.add_all(books)
    db_session.commit()
    db_session.add(BookCopy(book_id=books[0].id, status="in_shelf", location=SENTINEL_LOCATION))
    db_session.commit()
    # 一张假封面（字节级非图片，覆盖 PIL 解码失败回退原图路径）
    covers = __import__("app.config", fromlist=["settings"]).settings.covers_dir
    covers.mkdir(parents=True, exist_ok=True)
    (covers / "pub1.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-cover-bytes")
    return books


def _make_client(db_session: Session, host: str) -> TestClient:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    return TestClient(app, client=(host, 50000))


@pytest.fixture()
def c_mode(monkeypatch):
    """C 模式 + 家庭 LAN CIDR 的默认配置。"""
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "lan_shared")
    monkeypatch.setattr(settings, "trusted_lan_cidrs", "192.168.1.0/24")
    monkeypatch.setattr(settings, "public_catalog_rate_limit_per_minute", 60)


# ── 模式门控 ──


def test_disabled_mode_blocks_anonymous(db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "disabled")
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403
    assert r.json()["error"] == "ANONYMOUS_CATALOG_DISABLED"


def test_explicit_public_treated_as_disabled_in_stage1(db_session: Session, monkeypatch) -> None:
    """B 模式（explicit_public）属阶段 4：本期一律按关闭处理。"""
    from app.config import settings
    monkeypatch.setattr(settings, "anonymous_catalog_mode", "explicit_public")
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403
    assert r.json()["error"] == "ANONYMOUS_CATALOG_DISABLED"


# ── 信任门控 ──


def test_loopback_can_browse(c_mode, db_session: Session, seeded: list[Book]) -> None:
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["total"] == 2
    assert {i["title"] for i in data["items"]} == {"公开书一", "公开书二"}


def test_lan_cidr_peer_can_browse(c_mode, db_session: Session) -> None:
    c = _make_client(db_session, "192.168.1.77")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 200


def test_foreign_peer_downgraded(c_mode, db_session: Session) -> None:
    """公网来源：自动关闭 L1，只返回稳定错误码，不带任何书目数据。"""
    c = _make_client(db_session, "8.8.8.8")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403
    body = r.json()
    assert body["error"] == "LAN_REQUIRED"
    assert body["data"] is None
    assert "公开书" not in r.text


def test_private_but_unconfigured_lan_downgraded(c_mode, db_session: Session, monkeypatch) -> None:
    """10.x 不在 TRUSTED_LAN_CIDRS：不可信（不默认信任任意私网）。"""
    from app.config import settings
    monkeypatch.setattr(settings, "trusted_lan_cidrs", "192.168.1.0/24")
    c = _make_client(db_session, "10.0.0.9")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403
    assert r.json()["error"] == "LAN_REQUIRED"


def test_trusted_proxy_lan_client_can_browse(c_mode, db_session: Session, monkeypatch) -> None:
    """反代档：对端为可信代理，XFF 右值法还原出 LAN 客户端 → 可信。"""
    from app.config import settings
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    c = _make_client(db_session, "172.18.0.5")
    r = c.get(
        "/api/v1/public-catalog/books",
        headers={"X-Forwarded-For": "192.168.1.50, 172.18.0.5"},
    )
    assert r.status_code == 200


def test_trusted_proxy_forged_xff_rejected(c_mode, db_session: Session, monkeypatch) -> None:
    """伪造首跳在 LAN 内但网关追加了公网真实地址 → 拒绝（BUG-181 右值法）。"""
    from app.config import settings
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    c = _make_client(db_session, "172.18.0.5")
    r = c.get(
        "/api/v1/public-catalog/books",
        headers={"X-Forwarded-For": "192.168.1.50, 203.0.113.9, 172.18.0.5"},
    )
    assert r.status_code == 403
    assert r.json()["error"] == "LAN_REQUIRED"


def test_trusted_proxy_without_xff_downgraded(c_mode, db_session: Session, monkeypatch) -> None:
    """CHK-073/BUG-200：可信代理但无 XFF → 无法确认最终客户端，fail-closed。"""
    from app.config import settings
    monkeypatch.setattr(settings, "trusted_proxies", "172.18.0.0/16")
    c = _make_client(db_session, "172.18.0.5")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403
    assert r.json()["error"] == "LAN_REQUIRED"


def test_non_ip_peer_untrusted(c_mode, db_session: Session) -> None:
    """对端不是 IP（异常部署/测试 transport）→ 不可信。"""
    c = _make_client(db_session, "testclient")
    r = c.get("/api/v1/public-catalog/books")
    assert r.status_code == 403


# ── 白名单与脱敏 ──


def test_response_fields_strictly_whitelisted(c_mode, db_session: Session, seeded: list[Book]) -> None:
    from app.services.catalog_read import PUBLIC_CATALOG_FIELDS
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books")
    for item in r.json()["data"]["items"]:
        assert set(item.keys()) == set(PUBLIC_CATALOG_FIELDS)


def test_anonymous_response_has_no_sensitive_data(c_mode, db_session: Session, seeded: list[Book]) -> None:
    c = _make_client(db_session, "127.0.0.1")
    text = c.get("/api/v1/public-catalog/books").text
    text += c.get(f"/api/v1/public-catalog/books/{seeded[0].id}").text
    assert SENTINEL_LOCATION not in text
    assert SENTINEL_MEMBER not in text
    for key in ("cover_path", "file_path", "owner_member_id", "reading_progress",
                "reading_notes", "purchase_records", "isbn13", "extra"):
        assert key not in text


def test_availability_desensitized(c_mode, db_session: Session, seeded: list[Book]) -> None:
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books", params={"availability": "in_shelf"})
    items = r.json()["data"]["items"]
    assert [i["title"] for i in items] == ["公开书一"]
    assert items[0]["availability_status"] == "in_shelf"


# ── 详情与封面 ──


def test_detail_found_and_not_found(c_mode, db_session: Session, seeded: list[Book]) -> None:
    c = _make_client(db_session, "127.0.0.1")
    r = c.get(f"/api/v1/public-catalog/books/{seeded[0].id}")
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "公开书一"
    r = c.get("/api/v1/public-catalog/books/99999")
    assert r.status_code == 404
    assert r.json()["error"] == "BOOK_NOT_FOUND"


def test_cover_served_with_cache_headers(c_mode, db_session: Session, seeded: list[Book]) -> None:
    c = _make_client(db_session, "127.0.0.1")
    r = c.get(f"/api/v1/public-catalog/covers/{seeded[0].id}")
    assert r.status_code == 200
    assert "max-age" in r.headers.get("cache-control", "")
    # CHK-071：IP 门控资源禁止共享缓存
    assert "private" in r.headers.get("cache-control", "")


def test_books_response_cache_is_private(c_mode, db_session: Session) -> None:
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books")
    cc = r.headers.get("cache-control", "")
    assert "private" in cc and "public" not in cc


def test_public_tags_never_returned(c_mode, db_session: Session, seeded: list[Book]) -> None:
    """CHK-071：即使书上有标签，匿名输出也不下发（无公开分级前）。"""
    from sqlalchemy import select as sa_select
    from app.models import BookTag, Tag as TagModel
    from app.utils.book_helpers import serialize_json_list
    # 给公开书一挂一个家庭内部标签
    tag = TagModel(name="SENTINEL_TAG_内部流转")
    db_session.add(tag)
    db_session.commit()
    db_session.add(BookTag(book_id=seeded[0].id, tag_id=tag.id))
    db_session.commit()

    c = _make_client(db_session, "127.0.0.1")
    text = c.get("/api/v1/public-catalog/books").text
    text += c.get(f"/api/v1/public-catalog/books/{seeded[0].id}").text
    assert "SENTINEL_TAG_内部流转" not in text
    detail = c.get(f"/api/v1/public-catalog/books/{seeded[0].id}").json()["data"]
    assert detail["public_tags"] == []


# ── 共享安全审计（CHK-071 遗漏项 5） ──


def test_audit_denials_recorded_and_suppressed(c_mode, db_session: Session, seeded: list[Book]) -> None:
    from app.services import security_audit
    security_audit.reset()
    c = _make_client(db_session, "8.8.8.8")
    assert c.get("/api/v1/public-catalog/books").status_code == 403
    assert c.get("/api/v1/public-catalog/books").status_code == 403
    events = security_audit.list_security_events(db_session, event_type="public_catalog.access")
    denies = [e for e in events if '"outcome": "deny"' in (e.payload or "") and "LAN_REQUIRED" in (e.payload or "")]
    assert len(denies) == 1  # 60s 抑制窗口内聚合为一条
    security_audit.reset()


def test_audit_allow_sampled(c_mode, db_session: Session, seeded: list[Book]) -> None:
    from app.services import security_audit
    security_audit.reset()
    c = _make_client(db_session, "192.168.1.88")
    assert c.get("/api/v1/public-catalog/books").status_code == 200
    assert c.get("/api/v1/public-catalog/books").status_code == 200
    events = security_audit.list_security_events(db_session, event_type="public_catalog.access")
    allows = [e for e in events if '"outcome": "allow"' in (e.payload or "")]
    assert len(allows) == 1  # 放行采样：600s 窗口一条
    security_audit.reset()


def test_audit_rate_limited_recorded(c_mode, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    from app.services import security_audit
    security_audit.reset()
    monkeypatch.setattr(settings, "public_catalog_rate_limit_per_minute", 2)
    c = _make_client(db_session, "192.168.1.89")
    for _ in range(2):
        assert c.get("/api/v1/public-catalog/books").status_code == 200
    assert c.get("/api/v1/public-catalog/books").status_code == 429
    events = security_audit.list_security_events(db_session, event_type="public_catalog.access")
    assert any("RATE_LIMITED" in (e.payload or "") for e in events)
    security_audit.reset()
def test_cover_thumbnail_cache_hits_for_non_jpg(
    c_mode, db_session: Session, seeded: list[Book], monkeypatch
) -> None:
    """回归：非 jpg 封面第二次请求命中 .thumbs 缓存，不再重新编码。

    缓存命中检查与保存路径若不一致（如检查 .png、保存 .jpg），
    每次请求都会重新编码缩略图。此处把 Image.open 改为抛错：
    命中缓存则不触碰 PIL；未命中会走异常回退返回原 png 路径，断言失败。
    """
    from PIL import Image

    from app.api.v1.public_catalog import _ensure_thumbnail
    from app.config import settings

    src = settings.covers_dir / "pub2.png"
    Image.new("RGB", (800, 1200), "blue").save(src)

    first = _ensure_thumbnail(src)
    assert first.suffix == ".jpg" and first.is_file()

    def _no_decode(*args, **kwargs):
        raise AssertionError("cache miss: thumbnail re-encoded")

    monkeypatch.setattr(Image, "open", _no_decode)
    second = _ensure_thumbnail(src)
    assert second == first



def test_cover_missing_book_404(c_mode, db_session: Session) -> None:
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/covers/99999")
    assert r.status_code == 404
    assert r.json()["error"] == "COVER_NOT_FOUND"


def test_cover_untrusted_downgraded(c_mode, db_session: Session, seeded: list[Book]) -> None:
    c = _make_client(db_session, "8.8.8.8")
    r = c.get(f"/api/v1/public-catalog/covers/{seeded[0].id}")
    assert r.status_code == 403


# ── 分页与限流 ──


def test_page_size_capped(c_mode, db_session: Session) -> None:
    from app.config import settings
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/public-catalog/books", params={"page_size": 10_000})
    assert r.status_code == 200
    assert r.json()["data"]["page_size"] == settings.public_catalog_max_page_size


def test_rate_limit_returns_429(c_mode, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "public_catalog_rate_limit_per_minute", 3)
    c = _make_client(db_session, "192.168.1.60")
    codes = [c.get("/api/v1/public-catalog/books").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r4 = c.get("/api/v1/public-catalog/books")
    assert r4.status_code == 429
    assert r4.json()["error"] == "RATE_LIMITED"
    assert "Retry-After" in r4.headers


def test_rate_limit_isolated_per_ip(c_mode, db_session: Session, monkeypatch) -> None:
    from app.config import settings
    monkeypatch.setattr(settings, "public_catalog_rate_limit_per_minute", 2)
    a = _make_client(db_session, "192.168.1.61")
    for _ in range(2):
        assert a.get("/api/v1/public-catalog/books").status_code == 200
    assert a.get("/api/v1/public-catalog/books").status_code == 429
    b = _make_client(db_session, "192.168.1.62")
    assert b.get("/api/v1/public-catalog/books").status_code == 200


def test_business_books_endpoint_still_requires_auth(c_mode, db_session: Session) -> None:
    """回归：C 模式不放开完整业务 API——匿名 /api/v1/books 仍被拒。"""
    c = _make_client(db_session, "127.0.0.1")
    r = c.get("/api/v1/books")
    assert r.status_code in (401, 403)
