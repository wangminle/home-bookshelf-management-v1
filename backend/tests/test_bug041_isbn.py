"""BUG-041: ISBN 校验覆盖元数据入库与 recognize。"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.intake import IntakeInput, intake_book
from app.services.metadata.base import BookMetadata
from app.services.recognition import recognize_isbn_from_image
from app.utils.book_helpers import is_valid_isbn


INVALID_ISBN13 = "9780000000000"  # 长度正确但校验位错误
assert not is_valid_isbn(INVALID_ISBN13)


def test_intake_rejects_invalid_metadata_isbn(db_session):
    meta = BookMetadata(
        title="脏 ISBN 书",
        isbn13=INVALID_ISBN13,
        isbn10=None,
        authors=["作者"],
        source="mock",
    )
    with patch("app.services.intake.fetch_metadata", return_value=meta):
        result = intake_book(db_session, IntakeInput(title="脏 ISBN 书"))

    assert result.book.isbn13 is None or is_valid_isbn(result.book.isbn13)
    assert result.book.isbn13 != INVALID_ISBN13


def test_recognize_ignores_invalid_check_digit(tmp_path: Path):
    """条码解码出错误校验位时不应当作有效 ISBN 返回。"""
    img = tmp_path / "fake.jpg"
    img.write_bytes(b"not-a-real-image")

    class FakeSymbol:
        data = INVALID_ISBN13.encode()

    fake_image_mod = MagicMock()
    fake_image_mod.open.return_value.__enter__.return_value = object()
    fake_image_mod.open.return_value.__exit__.return_value = False

    fake_pyzbar = MagicMock()
    fake_pyzbar.decode.return_value = [FakeSymbol()]

    with patch.dict(
        "sys.modules",
        {
            "PIL": MagicMock(Image=fake_image_mod),
            "PIL.Image": fake_image_mod,
            "pyzbar": MagicMock(pyzbar=fake_pyzbar),
            "pyzbar.pyzbar": fake_pyzbar,
        },
    ):
        assert recognize_isbn_from_image(img) is None
