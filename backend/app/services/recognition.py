from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.utils.book_helpers import canonical_isbn13, is_valid_isbn, normalize_isbn

logger = logging.getLogger(__name__)

# BUG-149：pyzbar.decode 无原生超时，对高分辨率大图可阻塞数十秒甚至更久，
# 拖垮 intake/recognize 的 worker 线程表现为"接口一直不返回"。
# _RECOGNIZE_TIMEOUT_SEC 通过子进程/线程隔离给识别硬上限；超过则放弃并返回 None。
_RECOGNIZE_TIMEOUT_SEC = 15.0
# 识别前把图片长边缩到此像素以内：pyzbar 对超大图既慢又易漏检，
# 缩到 1600px 在保持条码可读的同时显著降低 decode 耗时。
_MAX_DECODE_DIM = 1600

# BI-02（契约 §2）：扫描结局码，与 warnings[].code 对应关系——
# ok→无警告；not_found→barcode_not_found；timeout→barcode_decode_timeout；
# unavailable→barcode_dependency_unavailable。
SCAN_OK = "ok"
SCAN_NOT_FOUND = "not_found"
SCAN_TIMEOUT = "timeout"
SCAN_UNAVAILABLE = "unavailable"


@dataclass
class BarcodeScanResult:
    """结构化条码扫描结局：入库据此降级并生成警告，而不是丢失原因。

    图片损坏仍抛 ValueError（不以降级掩盖上传错误，契约 §4.1）。
    """

    isbn: str | None = None
    outcome: str = SCAN_NOT_FOUND
    message: str = ""


def _decode_isbns(image_path: Path) -> list[str]:
    """实际调用 pyzbar 解码，返回所有合法 ISBN-13。可能抛 ImportError/RuntimeError。"""
    from PIL import Image
    from pyzbar.pyzbar import decode

    with Image.open(image_path) as img:
        # BUG-149：缩图降低 decode 耗时。convert("RGB") 统一通道，避免 RGBA/灰度差异。
        # getattr 守卫：部分测试用裸 mock 对象（无 .size/.thumbnail），跳过缩图直接 decode。
        size = getattr(img, "size", None)
        if size and max(size) > _MAX_DECODE_DIM:
            img.thumbnail((_MAX_DECODE_DIM, _MAX_DECODE_DIM))
        # convert 可能不在 mock 对象上；有则统一通道
        if hasattr(img, "convert"):
            img = img.convert("RGB")
        results: list[str] = []
        for symbol in decode(img):
            raw = symbol.data.decode("utf-8", errors="ignore")
            normalized = normalize_isbn(raw)
            if not normalized or not is_valid_isbn(normalized):
                continue
            results.append(canonical_isbn13(normalized))
        return results


def scan_isbn_from_image(image_path: Path) -> BarcodeScanResult:
    """扫描条码并返回结构化结局（BI-02）。依赖不可用不再抛异常，由调用方降级。"""
    try:
        from PIL import Image  # noqa: F401
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError:
        return BarcodeScanResult(
            outcome=SCAN_UNAVAILABLE,
            message="ISBN 条码识别需要安装 pyzbar 和 Pillow，且系统需安装 zbar 库（macOS: brew install zbar）",
        )

    # BUG-149：用线程池给 pyzbar.decode 套硬超时。
    # 线程无法被强杀，但 future.result(timeout) 会让主线程立即返回 None，
    # 解码线程继续在后台跑至结束——单次识别的孤儿线程可接受，且不会阻塞接口返回。
    # 关键：不使用 with 上下文——退出 with 会隐式 shutdown(wait=True)，
    # 超时后仍阻塞等待解码线程完成，硬超时形同虚设。改为手动管理执行器，
    # 超时或正常返回后立即 shutdown(wait=False, cancel_futures=True)。
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_decode_isbns, image_path)
    try:
        results = future.result(timeout=_RECOGNIZE_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        logger.warning(
            "ISBN 条码识别超时（%ss），放弃：%s", _RECOGNIZE_TIMEOUT_SEC, image_path
        )
        return BarcodeScanResult(
            outcome=SCAN_TIMEOUT,
            message=f"ISBN 条码识别超时（{_RECOGNIZE_TIMEOUT_SEC}s），已放弃",
        )
    except OSError as exc:
        raise ValueError(f"无法识别图片文件：{exc}") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    if results:
        return BarcodeScanResult(isbn=results[0], outcome=SCAN_OK, message="")
    return BarcodeScanResult(
        outcome=SCAN_NOT_FOUND, message="图片中未发现可解码的 ISBN 条码"
    )


def recognize_isbn_from_image(image_path: Path) -> str | None:
    """兼容入口：独立识别接口（/recognize/isbn）继续如实报告能力故障。

    依赖不可用抛 RuntimeError（API 层映射 503）——入库降级不意味着识别能力正常。
    """
    result = scan_isbn_from_image(image_path)
    if result.outcome == SCAN_UNAVAILABLE:
        raise RuntimeError(result.message)
    if result.outcome == SCAN_TIMEOUT:
        return None
    if result.outcome == SCAN_NOT_FOUND:
        return None
    return result.isbn
