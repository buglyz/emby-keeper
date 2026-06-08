from embykeeper.utils import truncate_str


def test_truncate_str_uses_requested_prefix_length():
    assert truncate_str("abcdefghijklmnop", 10) == "abcdefghij..."


def test_truncate_str_keeps_short_text():
    assert truncate_str("abc", 10) == "abc"
