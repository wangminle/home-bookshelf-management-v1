"""BUG-040: Pydantic 长度约束对齐 ORM。"""

import pytest
from pydantic import ValidationError

from app.schemas.book import BookCreate
from app.schemas.purchase import PurchaseCreate


def test_title_rejects_over_500():
    with pytest.raises(ValidationError):
        BookCreate(title="书" * 501)


def test_language_rejects_over_10():
    with pytest.raises(ValidationError):
        BookCreate(title="合法书名", language="x" * 11)


def test_currency_rejects_over_10():
    with pytest.raises(ValidationError):
        PurchaseCreate(price=12.0, currency="X" * 11)


def test_valid_lengths_ok():
    BookCreate(title="书" * 500, language="zh")
    PurchaseCreate(price=1.0, currency="CNY")
