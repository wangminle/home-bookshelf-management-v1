"""BI-01 契约测试：manifest v2 字段、v1 兼容读取、升级备份与高版本拒绝写回。

夹具：tests/fixtures/intake/manifest_v1_sample.json（正常历史 v1）、
     tests/fixtures/intake/manifest_v2_sample.json（正常/冲突/故障/结果未知样例）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import batch_manifest as bm  # noqa: E402

FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "intake"


def load_fixture(name: str, tmp_path: Path) -> Path:
    """夹具复制到临时目录后再操作，绝不改动仓库内原件。"""
    dst = tmp_path / name
    shutil.copy(FIXTURES / name, dst)
    return dst


# ── v1 兼容读取 ──

def test_v1_fixture_loads_and_upgrades_in_memory(tmp_path):
    path = load_fixture("manifest_v1_sample.json", tmp_path)
    manifest = bm.load_manifest(path)

    assert manifest["version"] == 1  # 内存中保持读入版本，写盘时置 2
    entries = manifest["entries"]
    assert len(entries) == 4
    # v1 条目全部可读，历史 result 原样保留（迁移不丢弃）
    assert entries[0]["result"]["book_id"] == 10
    assert entries[0]["result"]["message"] == "已入库《翦商》"
    # v2 缺省字段补齐
    assert [e["photo_id"] for e in entries] == ["p0001", "p0002", "p0003", "p0004"]
    assert all(e["role"] == "cover" for e in entries)
    assert all(e["confirmed_fields"] == [] for e in entries)
    assert all(e["warnings"] == [] for e in entries)
    assert all(e["candidate_id"] is None for e in entries)
    # 磁盘文件未被读取行为改动
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1 and "photo_id" not in on_disk["entries"][0]


def test_v1_save_creates_backup_and_writes_v2(tmp_path):
    path = load_fixture("manifest_v1_sample.json", tmp_path)
    original_bytes = path.read_bytes()
    manifest = bm.load_manifest(path)
    manifest["entries"][0]["title"] = "翦商（修订）"

    bm.save_manifest(path, manifest)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["batch_id"]  # 首次升级保存时固化批次 ID
    assert saved["entries"][0]["photo_id"] == "p0001"
    assert saved["entries"][0]["result"]["book_id"] == 10  # 历史 result 保留
    backup = tmp_path / "manifest_v1_sample.json.v1.bak"
    assert backup.exists() and backup.read_bytes() == original_bytes

    # 二次保存不再覆盖已有备份（保留最早原件）
    manifest["entries"][0]["title"] = "翦商（再改）"
    bm.save_manifest(path, manifest)
    assert backup.read_bytes() == original_bytes
    assert json.loads(path.read_text(encoding="utf-8"))["entries"][0]["title"] == "翦商（再改）"


def test_v2_load_save_roundtrip_preserves_all_fields(tmp_path):
    path = load_fixture("manifest_v2_sample.json", tmp_path)
    manifest = bm.load_manifest(path)

    assert manifest["version"] == 2
    assert manifest["batch_id"] == "batch-20260930-100000-a1b2"
    by_status = {e["status"]: e for e in manifest["entries"]}
    assert set(by_status) == {"imported", "failed", "outcome_unknown", "pending"}

    conflict = by_status["imported"]
    assert conflict["warnings"][0]["code"] == bm.WARNING_METADATA_FIELD_CONFLICT
    assert conflict["warnings"][0]["detail"]["metadata"] == "Revelation"
    assert conflict["result"]["run_id"] == "run-20260930-140001-c1d2"

    fault = by_status["failed"]
    assert fault["result"]["error_code"] == bm.ERROR_CODE_BARCODE_UNAVAILABLE
    assert fault["warnings"][0]["code"] == bm.WARNING_BARCODE_UNAVAILABLE

    unknown = by_status["outcome_unknown"]
    assert unknown["result"]["error_code"] == bm.ERROR_CODE_TIMEOUT

    bm.save_manifest(path, manifest)
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["entries"][0]["warnings"] == manifest["entries"][0]["warnings"]
    assert not (tmp_path / "manifest_v2_sample.json.v1.bak").exists()  # v2 不备份


def test_unsupported_future_version_rejected_without_writeback(tmp_path, capsys):
    path = tmp_path / "future.json"
    future = {
        "version": 3,
        "source_dir": str(tmp_path),
        "entries": [{"file": "a.jpg", "status": "pending"}],
    }
    raw = json.dumps(future, ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(SystemExit):
        bm.load_manifest(path)

    assert "不支持的清单版本" in capsys.readouterr().err
    # 绝不写回：文件内容保持逐字节不变
    assert path.read_text(encoding="utf-8") == raw


def test_save_refuses_unknown_high_version(tmp_path, capsys):
    path = tmp_path / "m.json"
    manifest = {"version": 3, "source_dir": str(tmp_path),
                "entries": [{"file": "a.jpg", "status": "pending", "photo_id": "p0001"}]}
    with pytest.raises(SystemExit):
        bm.save_manifest(path, manifest)
    assert "拒绝写出不支持的清单版本" in capsys.readouterr().err
    assert not path.exists()


def test_invalid_status_rejected(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(
        {"version": 2, "source_dir": str(tmp_path),
         "entries": [{"file": "a.jpg", "status": "weird"}]}), encoding="utf-8")
    with pytest.raises(SystemExit):
        bm.load_manifest(path)


# ── 错误码分类（CLI 与报告使用相同错误码）──

@pytest.mark.parametrize("message,expected", [
    ("[HTTP 400] ISBN 校验位不正确", bm.ERROR_CODE_INVALID_ISBN),
    ("[HTTP 400] 无法识别图片文件：[Errno 2] No such file", bm.ERROR_CODE_IMAGE_CORRUPTED),
    ("[HTTP 400] 价格必须大于 0", bm.ERROR_CODE_BAD_REQUEST),
    ("[HTTP 409] 记录已存在", bm.ERROR_CODE_CONFLICT),
    ("[HTTP 401] 未认证", bm.ERROR_CODE_UNAUTHORIZED),
    ("[HTTP 403] 无权限", bm.ERROR_CODE_FORBIDDEN),
    ("[HTTP 503] ISBN 条码识别需要安装 pyzbar 和 Pillow，且系统需安装 zbar 库（macOS: brew install zbar）",
     bm.ERROR_CODE_BARCODE_UNAVAILABLE),
    ("[HTTP 503] 服务暂不可用", bm.ERROR_CODE_SERVICE_UNAVAILABLE),
    ("连接 API 超时：http://127.0.0.1:8000（POST /books/intake）", bm.ERROR_CODE_TIMEOUT),
    ("回执丢失（可能已提交）：http://127.0.0.1:8000（POST /books/intake，ReadError）——请求可能已被受理",
     bm.ERROR_CODE_RECEIPT_LOST),
    ("回执丢失（可能已提交）：http://127.0.0.1:8000（POST /books/intake，RemoteProtocolError）",
     bm.ERROR_CODE_RECEIPT_LOST),
    ("回执丢失（可能已提交）：http://127.0.0.1:8000（POST /books/intake，HTTP 200 响应体不可解析）",
     bm.ERROR_CODE_RECEIPT_LOST),
    ("无法连接 API：http://127.0.0.1:8000（ConnectError）", bm.ERROR_CODE_NETWORK_ERROR),
    ("API 返回非 JSON（502）: <html>", bm.ERROR_CODE_NETWORK_ERROR),
    ("缺少 isbn 和 title，无法入库", bm.ERROR_CODE_MISSING_INPUT),
    ("封面文件缺失: IMG_0001.jpg", bm.ERROR_CODE_FILE_MISSING),
    ("某种未知错误", bm.ERROR_CODE_UNKNOWN),
    ("", bm.ERROR_CODE_UNKNOWN),
])
def test_classify_client_error(message, expected):
    assert bm.classify_client_error(message) == expected


def test_timeout_is_outcome_unknown_others_are_not():
    assert bm.is_outcome_unknown_error(bm.ERROR_CODE_TIMEOUT)
    # BUG-261：回执丢失（ReadError/RemoteProtocolError/2xx 不可解析）同属结果未知，
    # 不得被 retry-failed 自动重发；确定未提交的 network_error 可安全重试
    assert bm.is_outcome_unknown_error(bm.ERROR_CODE_RECEIPT_LOST)
    assert not bm.is_outcome_unknown_error(bm.ERROR_CODE_NETWORK_ERROR)
    assert not bm.is_outcome_unknown_error(bm.ERROR_CODE_BAD_REQUEST)


# ── photo_id 与哈希 ──

def test_new_photo_id_continues_from_max():
    assert bm.new_photo_id([]) == "p0001"
    assert bm.new_photo_id(["p0001", "p0002"]) == "p0003"
    assert bm.new_photo_id(["p0009", None, "legacy"]) == "p0010"


def test_sha256_of_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello")
    digest = bm.sha256_of_file(f)
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert bm.sha256_of_file(tmp_path / "missing.bin") is None


def test_run_and_batch_ids_are_unique_and_shaped():
    ids = {bm.new_run_id() for _ in range(20)}
    assert len(ids) == 20 and all(i.startswith("run-") for i in ids)
    assert all(b.startswith("batch-") for b in (bm.new_batch_id() for _ in range(5)))


# ── 与批量脚本的集成：scan 落 v2 字段 ──

def test_scan_assigns_stable_photo_ids_across_merges(tmp_path):
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import batch_import_covers as bic

    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "b.jpg").write_bytes(b"\xff\xd8fake-jpg")
    out = tmp_path / "m.json"
    bic.main(["scan", "--dir", str(covers), "--out", str(out)])
    first = json.loads(out.read_text(encoding="utf-8"))
    assert first["version"] == 2
    assert first["entries"][0]["photo_id"] == "p0001"
    assert first["entries"][0]["sha256"]  # scan 固化内容哈希
    assert first["batch_id"]

    # 目录新增照片后再扫：已有 photo_id 不变，新条目延续编号
    (covers / "a.jpg").write_bytes(b"\xff\xd8fake-jpg2")
    bic.main(["scan", "--dir", str(covers), "--out", str(out)])
    second = json.loads(out.read_text(encoding="utf-8"))
    by_file = {e["file"]: e for e in second["entries"]}
    assert by_file["b.jpg"]["photo_id"] == "p0001"
    assert by_file["a.jpg"]["photo_id"] == "p0002"
