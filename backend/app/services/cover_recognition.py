from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.services.metadata import fetch_metadata
from app.services.recognition import recognize_isbn_from_image
from app.services.storage import save_uploaded_image
from app.utils.book_helpers import normalize_title


def _cover_target_name(title: str | None) -> str:
    if title and title.strip():
        return normalize_title(title)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"cover_scan_{stamp}_{uuid4().hex[:8]}"


@dataclass
class CoverRecognizeResult:
    found: bool
    isbn13: str | None
    title: str | None
    authors: list[str] | None
    publisher: str | None
    cover_path: str | None
    matched_source: str | None
    message: str


def recognize_cover(
    image_path: Path,
    *,
    title: str | None = None,
    author: str | None = None,
) -> CoverRecognizeResult:
    saved = save_uploaded_image(image_path, target_name=_cover_target_name(title))

    isbn13 = recognize_isbn_from_image(image_path)
    if isbn13:
        metadata = fetch_metadata(isbn=isbn13)
        if metadata:
            return CoverRecognizeResult(
                found=True,
                isbn13=isbn13,
                title=metadata.title,
                authors=metadata.authors,
                publisher=metadata.publisher,
                cover_path=saved,
                matched_source=metadata.source,
                message=f"识别到 ISBN {isbn13}，匹配《{metadata.title}》",
            )
        return CoverRecognizeResult(
            found=True,
            isbn13=isbn13,
            title=None,
            authors=None,
            publisher=None,
            cover_path=saved,
            matched_source=None,
            message=f"识别到 ISBN {isbn13}，未在外部书目源找到元数据",
        )

    search_title = title.strip() if title else None
    search_author = author.strip() if author else None
    if search_title:
        metadata = fetch_metadata(title=search_title, author=search_author)
        if metadata:
            return CoverRecognizeResult(
                found=True,
                isbn13=metadata.isbn13,
                title=metadata.title,
                authors=metadata.authors,
                publisher=metadata.publisher,
                cover_path=saved,
                matched_source=metadata.source,
                message=f"按书名匹配到《{metadata.title}》",
            )

    return CoverRecognizeResult(
        found=False,
        isbn13=None,
        title=search_title,
        authors=[search_author] if search_author else None,
        publisher=None,
        cover_path=saved,
        matched_source=None,
        message="未识别到条码；请提供书名/作者或交由 Agent 视觉识别后重试",
    )
