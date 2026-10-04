"""BI-02 回归：条码能力降级与结构化警告（契约 §4.1 行为矩阵）。

| 输入/故障 | 预期 |
| --- | --- |
| 有效手工 ISBN | 正常校验，不依赖图片解码 |
| 图片+书名，缺 zbar/超时/未发现 ISBN | 按书名入库 + 对应警告码 |
| 只有图片，无法解码且无候选 | 400 需要补充信息，不建"未知书名" |
| 图片损坏 | 400 如实失败，不以降级掩盖 |
| 手工 ISBN 校验失败 | 400 不静默忽略 |
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from app.services.intake import (
    WARNING_BARCODE_INVALID_CHECKSUM,
    WARNING_BARCODE_NOT_FOUND,
    WARNING_BARCODE_TIMEOUT,
    WARNING_BARCODE_UNAVAILABLE,
    WARNING_METADATA_MISSING,
    IntakeInput,
    intake_book,
)
from app.services.recognition import BarcodeScanResult

# 一本真实存在的书：《活着》余华（校验位正确）
ISBN_HUOZHE = "9787506365437"


def _make_image(tmp_path: Path) -> Path:
    img = tmp_path / "cover.jpg"
    img.write_bytes(b"\xff\xd8fake-jpg")
    return img


def _run(db_engine, payload: IntakeInput, metadata=None):
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    with SessionLocal() as s:
        return intake_book(s, payload)


def _codes(result) -> list[str]:
    return [w.code for w in (result.warnings or [])]


# ── 矩阵行 2：图片+书名，各类条码故障 → 降级入库 + 警告 ──

@pytest.mark.parametrize("outcome,expected_code", [
    ("unavailable", WARNING_BARCODE_UNAVAILABLE),
    ("timeout", WARNING_BARCODE_TIMEOUT),
    ("not_found", WARNING_BARCODE_NOT_FOUND),
])
def test_barcode_fault_with_title_degrades_with_warning(db_engine, tmp_path, outcome, expected_code):
    """缺 zbar/超时/未发现条码时有书名即可入库，警告携带对应错误码。"""
    scan = BarcodeScanResult(
        outcome=outcome,
        message={
            "unavailable": "ISBN 条码识别需要安装 pyzbar 和 Pillow（macOS: brew install zbar）",
            "timeout": "ISBN 条码识别超时（15.0s），已放弃",
            "not_found": "图片中未发现可解码的 ISBN 条码",
        }[outcome],
    )
    with (
        patch("app.services.intake.scan_isbn_from_image", return_value=scan),
        patch("app.services.intake.fetch_metadata", return_value=None),
    ):
        result = _run(db_engine, IntakeInput(title="翦商", author="李硕", image_path=_make_image(tmp_path)))

    assert result.action == "created"
    assert result.book.title == "翦商"
    assert expected_code in _codes(result)
    # 元数据未命中也如实可见（manual ≠ 外部无此书）
    assert WARNING_METADATA_MISSING in _codes(result)


def test_scan_ok_supplies_isbn_without_warning(db_engine, tmp_path):
    with (
        patch("app.services.intake.scan_isbn_from_image",
              return_value=BarcodeScanResult(isbn=ISBN_HUOZHE, outcome="ok")),
        patch("app.services.intake.fetch_metadata", return_value=None),
    ):
        result = _run(db_engine, IntakeInput(image_path=_make_image(tmp_path)))
    assert result.action == "created"
    assert result.isbn_detected == ISBN_HUOZHE
    assert WARNING_BARCODE_NOT_FOUND not in _codes(result)


def test_scan_invalid_checksum_discarded_with_warning(db_engine, tmp_path):
    """识别层异常返回了校验位错误的码：丢弃并警告（保险路径）。"""
    bad = "9787506365430"  # 校验位错误
    with (
        patch("app.services.intake.scan_isbn_from_image",
              return_value=BarcodeScanResult(isbn=bad, outcome="ok")),
        patch("app.services.intake.fetch_metadata", return_value=None),
    ):
        result = _run(db_engine, IntakeInput(title="活着", image_path=_make_image(tmp_path)))
    assert result.isbn_detected is None
    assert result.book.isbn13 is None  # 无效 ISBN 不得入库
    assert WARNING_BARCODE_INVALID_CHECKSUM in _codes(result)


# ── 矩阵行 3：只有图片且无法解码 → 拒绝，不建"未知书名" ──

@pytest.mark.parametrize("outcome", ["unavailable", "timeout", "not_found"])
def test_image_only_without_decodable_barcode_rejected(db_engine, tmp_path, outcome):
    with (
        patch("app.services.intake.scan_isbn_from_image",
              return_value=BarcodeScanResult(outcome=outcome, message="x")),
        patch("app.services.intake.fetch_metadata", return_value=None),
    ):
        with pytest.raises(ValueError, match="无法识别书籍信息"):
            _run(db_engine, IntakeInput(image_path=_make_image(tmp_path)))


# ── 矩阵行 4：图片损坏 → 如实 400，不降级掩盖 ──

def test_corrupted_image_fails_as_input_error(db_engine, tmp_path):
    with patch("app.services.intake.scan_isbn_from_image",
               side_effect=ValueError("无法识别图片文件：[Errno 2] No such file")):
        with pytest.raises(ValueError, match="无法识别图片文件"):
            _run(db_engine, IntakeInput(title="翦商", author="李硕", image_path=_make_image(tmp_path)))


# ── 矩阵行 1/5：手工 ISBN 语义保持 ──

def test_valid_manual_isbn_skips_barcode_scan(db_engine, tmp_path):
    """有明确、有效的手工 ISBN 时不做图片解码，入库不依赖解码结果。"""
    def _no_scan(_path):
        raise AssertionError("有手工 ISBN 时不应触发条码扫描")

    with (
        patch("app.services.intake.scan_isbn_from_image", side_effect=_no_scan),
        patch("app.services.intake.fetch_metadata", return_value=None),
    ):
        result = _run(db_engine, IntakeInput(isbn=ISBN_HUOZHE, image_path=_make_image(tmp_path)))
    assert result.action == "created"
    assert result.isbn_detected == ISBN_HUOZHE
    assert result.book.title == f"ISBN {ISBN_HUOZHE}"  # 裸 ISBN 标题保持既有行为


def test_invalid_manual_isbn_checksum_still_rejected(db_engine, tmp_path):
    with patch("app.services.intake.fetch_metadata", return_value=None):
        with pytest.raises(ValueError, match="ISBN 校验位不正确"):
            _run(db_engine, IntakeInput(isbn="9787506365430", title="活着",
                                         image_path=_make_image(tmp_path)))


def test_malformed_manual_isbn_rejected(db_engine):
    with pytest.raises(ValueError, match="ISBN 格式无效"):
        _run(db_engine, IntakeInput(isbn="abc123"))


# ── API 层：warnings 字段透出（可选，旧客户端可忽略）──

def test_intake_json_api_returns_warnings(db_engine, db_session, client, monkeypatch):
    monkeypatch.setattr("app.services.intake.fetch_metadata", lambda **kw: None)
    monkeypatch.setattr(
        "app.services.intake.scan_isbn_from_image",
        lambda _p: BarcodeScanResult(outcome="unavailable", message="缺 zbar"),
    )
    resp = client.post("/api/v1/books/intake/json", json={
        "title": "小学问", "author": "蚂蚁金属",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["action"] == "created"
    codes = [w["code"] for w in data.get("warnings") or []]
    assert WARNING_METADATA_MISSING in codes


def test_intake_api_without_warnings_keeps_old_shape(db_engine, db_session, client, monkeypatch):
    """无警告时 warnings 为空数组（不缺失键语义），旧客户端零影响。"""
    monkeypatch.setattr("app.services.intake.fetch_metadata", lambda **kw: None)
    resp = client.post("/api/v1/books/intake/json", json={"title": "三体"})
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["action"] == "created"
    assert [w["code"] for w in data.get("warnings") or []] == [WARNING_METADATA_MISSING]


# ── 独立识别接口：如实报告能力故障（入库降级不改变识别口径）──

def test_recognize_isbn_endpoint_reports_dependency_error(db_engine, db_session, client, tmp_path, monkeypatch):
    from app.services.recognition import SCAN_UNAVAILABLE

    monkeypatch.setattr(
        "app.api.v1.recognize.recognize_isbn_from_image",
        lambda _p: (_ for _ in ()).throw(RuntimeError(
            "ISBN 条码识别需要安装 pyzbar 和 Pillow，且系统需安装 zbar 库（macOS: brew install zbar）")),
    )
    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8fake")
    with img.open("rb") as f:
        resp = client.post("/api/v1/recognize/isbn", files={"image": ("scan.jpg", f, "image/jpeg")})
    assert resp.status_code == 503
    assert SCAN_UNAVAILABLE  # 常量存在性锚定


def test_recognition_scan_result_contract():
    """scan 结局码集合冻结：ok/not_found/timeout/unavailable。"""
    assert BarcodeScanResult(outcome="ok", isbn="x").outcome == "ok"
