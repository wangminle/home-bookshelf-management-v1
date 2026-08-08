"""BUG-078: publish_date 只校验格式不校验真实日期合法性。

修复后 YYYY / YYYY-MM / YYYY-MM-DD 仍允许，但月13、2月30、非闰年2月29 等非法日期被拒。
"""

import pytest
from pydantic import ValidationError

from app.schemas.book import BookCreate


def test_publish_date_accepts_year_only():
    b = BookCreate(title="x", publish_date="2020")
    assert b.publish_date == "2020"


def test_publish_date_accepts_year_month():
    b = BookCreate(title="x", publish_date="2020-06")
    assert b.publish_date == "2020-06"


def test_publish_date_accepts_valid_full_date():
    BookCreate(title="x", publish_date="2021-06-15")


def test_publish_date_accepts_leap_day_in_leap_year():
    BookCreate(title="x", publish_date="2020-02-29")


def test_publish_date_rejects_non_date_string():
    with pytest.raises(ValidationError):
        BookCreate(title="x", publish_date="whatever-stg")


def test_publish_date_rejects_invalid_month():
    with pytest.raises(ValidationError):
        BookCreate(title="x", publish_date="2020-13-45")


def test_publish_date_rejects_feb_30():
    with pytest.raises(ValidationError):
        BookCreate(title="x", publish_date="2020-02-30")


def test_publish_date_rejects_leap_day_in_non_leap_year():
    with pytest.raises(ValidationError):
        BookCreate(title="x", publish_date="2021-02-29")


def test_publish_date_rejects_slash_format():
    with pytest.raises(ValidationError):
        BookCreate(title="x", publish_date="2020/06/15")


def test_publish_date_allows_empty():
    b = BookCreate(title="x", publish_date="")
    assert b.publish_date is None
