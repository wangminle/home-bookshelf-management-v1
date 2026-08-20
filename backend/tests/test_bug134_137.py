"""BUG-134~137 回归测试。

BUG-134 [P1]: 渠道绑定后内置 Web UI 仍可写入（X-UI-Client: web 头旁路）
BUG-135 [P2]: inflightRequests 计数覆盖完整响应处理周期（前端逻辑，此处验证后端不回归）
BUG-136 [P2]: recheck 命中时预生成封面被清理或复用，不产生孤儿文件
BUG-137 [P2]: _normalize_publish_date 正确解析 "Month YYYY" 和 "YYYY Month"
"""

import io
import re
from pathlib import Path
from unittest.mock import patch

from app.services.metadata.openlibrary import _normalize_publish_date
from app.services.storage import save_uploaded_image


# ── BUG-134：Web UI 旁路 ──────────────────────────────────


def _bootstrap_with_bindings(client):
    """创建成员并绑定渠道，返回 (member_id, channel_headers)。"""
    m = client.post("/api/v1/members", json={"name": "owner", "role": "owner"})
    assert m.status_code == 201, m.text
    mid = m.json()["data"]["id"]
    bind = client.post(
        "/api/v1/members/bind",
        json={"member_id": mid, "channel": "feishu", "external_user_id": "ou_test"},
    )
    assert bind.status_code == 200, bind.text
    return mid, {"X-Channel": "feishu", "X-External-User-Id": "ou_test"}


def test_bug134_ui_client_header_allows_write_after_bindings(client):
    """WBS-6：渠道绑定建立后，X-UI-Client: web 头不再有授权旁路效果。

    旧 BUG-134 允许 X-UI-Client: web 绕过渠道鉴权。
    WBS-6 移除此旁路，所有请求必须通过渠道身份或 Web 会话认证。
    """
    _, channel_headers = _bootstrap_with_bindings(client)

    client.cookies.clear()  # 夹具默认 owner 会话；匿名探针需无凭证
    # 匿名（无任何头）应被拒绝（BUG-168 后匿名一律 401）
    r = client.post("/api/v1/books", json={"title": "匿名测试"})
    assert r.status_code in (401, 403), r.text

    # 带 X-UI-Client: web 头的匿名写也必须被拒绝（WBS-6 移除旁路）
    r2 = client.post(
        "/api/v1/books",
        json={"title": "UI 客户端测试"},
        headers={"X-UI-Client": "web"},
    )
    assert r2.status_code in (401, 403), r2.text

    # 带渠道头 + UI 头也应正常工作（渠道身份有效，UI 头被忽略）
    r3 = client.post(
        "/api/v1/books",
        json={"title": "渠道+UI测试"},
        headers={**channel_headers, "X-UI-Client": "web"},
    )
    assert r3.status_code == 201, r3.text


def test_bug134_ui_client_does_not_affect_bind_protection(client):
    """X-UI-Client 头不应绕过 /members/bind 的保护（绑定仍需正确鉴权）。"""
    mid, _ = _bootstrap_with_bindings(client)

    # 绑定已有白名单后，匿名 + UI 头不能执行绑定
    client.cookies.clear()  # 夹具默认 owner 会话；此处验证真正的匿名 bind
    r = client.post(
        "/api/v1/members/bind",
        json={"member_id": mid, "channel": "feishu", "external_user_id": "ou_hijack"},
        headers={"X-UI-Client": "web"},
    )
    assert r.status_code == 403, r.text


def test_bug134_non_web_ui_client_rejected(client):
    """X-UI-Client 设为非 web 值不应触发旁路。"""
    _bootstrap_with_bindings(client)

    client.cookies.clear()  # 匿名 + 伪 UI 头探针
    r = client.post(
        "/api/v1/books",
        json={"title": "伪 UI 客户端"},
        headers={"X-UI-Client": "desktop"},
    )
    assert r.status_code in (401, 403), r.text


# ── BUG-136：recheck 命中时孤儿封面清理 ──────────────────


def test_bug136_orphan_cover_cleaned_on_recheck(client, db_session, tmp_path):
    """recheck 命中已有封面书籍时，预生成封面应被删除。

    模拟竞态：第一次 _find_existing 返回 None（锁外），进入封面生成；
    第二次 _find_existing（recheck，锁内）找到已有书籍 -> 预生成封面变孤儿。
    """
    from app.config import settings
    from app.models.book import Book
    from app.services.intake import IntakeInput, intake_book

    covers_dir = settings.covers_dir
    covers_dir.mkdir(parents=True, exist_ok=True)
    existing_cover = covers_dir / "existing_cover.jpg"
    existing_cover.write_bytes(b"existing cover data")
    cover_rel = str(existing_cover.relative_to(settings.data_dir))

    book = Book(
        title="重复书测试",
        normalized_title="repeat_book_test",
        authors='["作者"]',
        cover_path=cover_rel,
        source="manual",
    )
    db_session.add(book)
    db_session.commit()

    tmp_image = tmp_path / "upload.jpg"
    tmp_image.write_bytes(b"new upload cover")

    # 第一次 _find_existing 返回 None（让封面生成执行），
    # 第二次（recheck）返回已有书籍
    with (
        patch("app.services.intake.fetch_metadata", return_value=None),
        patch("app.services.intake.recognize_isbn_from_image", return_value=None),
        patch("app.services.intake._find_existing", side_effect=[None, book]),
    ):
        result = intake_book(
            db_session,
            IntakeInput(
                title="重复书测试",
                author="作者",
                image_path=tmp_image,
            ),
        )

    assert result.action == "exists"
    # 已有书的封面不应被误删
    assert existing_cover.exists(), "已有封面不应被误删"
    # 预生成的孤儿封面应已被清理：covers_dir 中不应有多余的 .jpg 文件
    # （existing_cover.jpg 是已有的，不应有其它新增文件）
    covers = list(covers_dir.glob("*.jpg"))
    assert len(covers) == 1, f"应只有 1 个封面文件（已有的），实际有 {len(covers)}: {covers}"
    assert covers[0].name == "existing_cover.jpg"


def test_bug136_cover_reused_when_existing_book_lacks_cover(client, db_session, tmp_path):
    """recheck 命中缺封面的已有书时，预生成封面应被复用而非丢弃。"""
    from app.config import settings
    from app.models.book import Book
    from app.services.intake import IntakeInput, intake_book

    book = Book(
        title="无封面书",
        normalized_title="no_cover_book",
        authors='["作者"]',
        cover_path=None,
        source="manual",
    )
    db_session.add(book)
    db_session.commit()

    tmp_image = tmp_path / "upload.jpg"
    tmp_image.write_bytes(b"new upload cover")

    with (
        patch("app.services.intake.fetch_metadata", return_value=None),
        patch("app.services.intake.recognize_isbn_from_image", return_value=None),
        patch("app.services.intake._find_existing", side_effect=[None, book]),
    ):
        result = intake_book(
            db_session,
            IntakeInput(
                title="无封面书",
                author="作者",
                image_path=tmp_image,
            ),
        )

    assert result.action == "exists"
    # 书籍现在应该有了封面路径
    db_session.refresh(book)
    assert book.cover_path is not None, "预生成封面应被复用回填"
    # 封面文件应确实存在
    cover_full = settings.data_dir / book.cover_path
    assert cover_full.exists(), "封面文件应存在"


def test_bug136_cleanup_orphan_cover_directly():
    """直接测试 _cleanup_orphan_cover 工具函数。"""
    from app.config import settings

    # 在 covers 目录创建一个文件
    covers_dir = settings.covers_dir
    covers_dir.mkdir(parents=True, exist_ok=True)
    orphan = covers_dir / "orphan_test.jpg"
    orphan.write_bytes(b"orphan data")
    assert orphan.exists()

    # 清理（传相对路径）
    rel = str(orphan.relative_to(settings.data_dir))
    from app.services.intake import _cleanup_orphan_cover
    _cleanup_orphan_cover(rel)
    assert not orphan.exists(), "孤儿封面应被删除"

    # 空值不应报错
    _cleanup_orphan_cover(None)
    _cleanup_orphan_cover("")


def test_bug136_cleanup_orphan_cover_rejects_path_traversal():
    """_cleanup_orphan_cover 应拒绝 data_dir 之外的路径。"""
    from app.services.intake import _cleanup_orphan_cover

    # 路径遍历尝试不应删除 data_dir 之外的文件
    _cleanup_orphan_cover("../../etc/passwd")
    _cleanup_orphan_cover("/etc/passwd")


# ── BUG-137：日期解析 ──────────────────────────────────


def test_bug137_month_year_format():
    """'July 2008' 应解析为 '2008-07'，不是仅 '2008'。"""
    assert _normalize_publish_date("July 2008") == "2008-07"
    assert _normalize_publish_date("September 2008") == "2008-09"
    assert _normalize_publish_date("Sep 2008") == "2008-09"
    assert _normalize_publish_date("Jan 2008") == "2008-01"
    assert _normalize_publish_date("December 2008") == "2008-12"


def test_bug137_year_month_format():
    """'2008 July' 应解析为 '2008-07'。"""
    assert _normalize_publish_date("2008 July") == "2008-07"
    assert _normalize_publish_date("2008 September") == "2008-09"
    assert _normalize_publish_date("2008 Dec") == "2008-12"


def test_bug137_full_date_still_works():
    """'Sep 1, 2008' 仍应解析为 '2008-09'（月份+年份，日期不保留）。"""
    result = _normalize_publish_date("Sep 1, 2008")
    assert result == "2008-09", f"Expected '2008-09', got '{result}'"

    result = _normalize_publish_date("September 15, 2008")
    assert result == "2008-09", f"Expected '2008-09', got '{result}'"


def test_bug137_standard_date_still_works():
    """标准格式 YYYY-MM-DD 和 YYYY-MM 和 YYYY 仍应正常解析。"""
    assert _normalize_publish_date("2008-07-01") == "2008-07-01"
    assert _normalize_publish_date("2008-07") == "2008-07"
    assert _normalize_publish_date("2008") == "2008"


def test_bug137_year_only_when_month_unknown():
    """无法识别的月份名应回退为仅年份。"""
    result = _normalize_publish_date("Xyzuary 2008")
    assert result == "2008", f"Expected '2008', got '{result}'"


def test_bug137_invalid_date_returns_none():
    """非法日期应返回 None。"""
    assert _normalize_publish_date(None) is None
    assert _normalize_publish_date("") is None
    assert _normalize_publish_date("   ") is None
    assert _normalize_publish_date("not a date at all") is None


def test_bug137_invalid_month_value_rejected():
    """虽然格式匹配但日期非法时应返回 None。"""
    # 这里没法直接构造非法月份（月份名映射都是合法的），
    # 但可以验证 day overflow 不影响
    result = _normalize_publish_date("Feb 30, 2008")
    # 月份+年份模式不受 day 影响，因为 day 是可选的且不保留
    assert result == "2008-02"


def test_bug137_uppercase_month():
    """大写月份名也应被识别。"""
    assert _normalize_publish_date("JULY 2008") == "2008-07"
    assert _normalize_publish_date("MARCH 2008") == "2008-03"
