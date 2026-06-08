import pytest

from embykeeper.utils import batch, format_byte_human, truncate_str


def test_truncate_str_uses_requested_prefix_length():
    assert truncate_str("abcdefghijklmnop", 10) == "abcdefghij..."


def test_truncate_str_keeps_short_text():
    assert truncate_str("abc", 10) == "abc"


def test_batch_splits_iterable():
    assert list(batch([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_batch_rejects_non_positive_size():
    with pytest.raises(ValueError):
        list(batch([1, 2, 3], 0))


def test_format_byte_human_uses_byte_pluralization():
    assert format_byte_human(1) == "1 Byte"
    assert format_byte_human(2) == "2 Bytes"
