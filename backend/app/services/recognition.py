from __future__ import annotations

from pathlib import Path

from app.utils.book_helpers import normalize_isbn


def recognize_isbn_from_image(image_path: Path) -> str | None:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
    except ImportError as exc:
        raise RuntimeError(
            "ISBN 条码识别需要安装 pyzbar 和 Pillow，且系统需安装 zbar 库（macOS: brew install zbar）"
        ) from exc

    with Image.open(image_path) as img:
        for symbol in decode(img):
            raw = symbol.data.decode("utf-8", errors="ignore")
            normalized = normalize_isbn(raw)
            if normalized:
                if len(normalized) == 10:
                    from app.utils.book_helpers import isbn10_to_isbn13

                    return isbn10_to_isbn13(normalized)
                return normalized
    return None
