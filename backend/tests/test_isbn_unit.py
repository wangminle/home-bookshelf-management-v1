"""TST-001: ISBN 工具函数单元测试——normalize/isbn10→13/is_valid/lookup_keys。

纯函数测试，不需要 client 或 DB fixture。
"""

from app.utils.book_helpers import (
    isbn10_to_isbn13,
    isbn_lookup_keys,
    is_valid_isbn,
    normalize_isbn,
)


def test_normalize_isbn_strips_hyphens():
    assert normalize_isbn("978-0-306-40615-7") == "9780306406157"


def test_normalize_isbn_isbn10_uppercase_x():
    assert normalize_isbn("080442957x") == "080442957X"


def test_normalize_isbn_isbn13_passthrough():
    assert normalize_isbn("9780306406157") == "9780306406157"


def test_normalize_isbn_non_string_safe():
    # BUG-071 修复点：非字符串入参不再崩溃，而是转 str
    assert normalize_isbn(9780306406157) == "9780306406157"


def test_normalize_isbn_none_returns_none():
    assert normalize_isbn(None) is None


def test_normalize_isbn_wrong_length_returns_none():
    assert normalize_isbn("12345") is None


def test_isbn10_to_isbn13_check_digit():
    assert isbn10_to_isbn13("0306406152") == "9780306406157"


def test_isbn10_to_isbn13_with_x():
    # 080442957X → 9780804429573（X 校验位不影响 13 位重新计算）
    assert isbn10_to_isbn13("080442957X") == "9780804429573"
    # 反向验证：转换结果应是合法 ISBN-13
    assert is_valid_isbn("9780804429573") is True


def test_is_valid_isbn_accepts_valid_isbn13():
    assert is_valid_isbn("9780306406157") is True


def test_is_valid_isbn_accepts_valid_isbn10():
    assert is_valid_isbn("0306406152") is True


def test_is_valid_isbn_rejects_wrong_check_digit():
    assert is_valid_isbn("9780000000000") is False


def test_is_valid_isbn_rejects_garbage():
    assert is_valid_isbn("abc") is False
    assert is_valid_isbn("") is False
    assert is_valid_isbn(None) is False


def test_isbn_lookup_keys_isbn13_single_key():
    keys = isbn_lookup_keys("9780306406157")
    assert keys == {"9780306406157"}


def test_isbn_lookup_keys_isbn10_both_forms():
    keys = isbn_lookup_keys("0306406152")
    assert "0306406152" in keys
    assert "9780306406157" in keys
    assert len(keys) == 2


def test_isbn_lookup_keys_garbage_empty():
    assert isbn_lookup_keys("not-an-isbn") == set()
