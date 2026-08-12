from __future__ import annotations

import logging
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


def recognize_isbn_from_image(image_path: Path) -> str | None:
    try:
        from PIL import Image  # noqa: F401
        from pyzbar.pyzbar import decode  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "ISBN 条码识别需要安装 pyzbar 和 Pillow，且系统需安装 zbar 库（macOS: brew install zbar）"
        ) from exc

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
        return None
    except OSError as exc:
        raise ValueError(f"无法识别图片文件：{exc}") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results[0] if results else None
