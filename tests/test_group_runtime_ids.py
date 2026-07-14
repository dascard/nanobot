def test_normalize_group_session_id():
    from core.group_runtime.ids import normalize_group_session_id

    assert normalize_group_session_id("123456") == "group_123456"
    assert normalize_group_session_id("group_123456") == "group_123456"
    assert normalize_group_session_id("qq:123456:group") == "group_123456"


def test_normalize_group_stream_id():
    from core.group_runtime.ids import normalize_group_stream_id

    assert normalize_group_stream_id(123456) == "qq:123456:group"
    assert normalize_group_stream_id("group_123456") == "qq:123456:group"
    assert normalize_group_stream_id("qq:123456:group") == "qq:123456:group"
    assert normalize_group_stream_id("123456") == "qq:123456:group"
    assert normalize_group_stream_id("") == ""


def test_normalize_group_stream_id_uses_canonical_encoding():
    from core.group_runtime.ids import normalize_group_stream_id

    assert normalize_group_stream_id("群:研发") == (
        "qq:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"
    )


def test_raw_group_id():
    from core.group_runtime.ids import raw_group_id

    assert raw_group_id("group_789") == "789"
    assert raw_group_id("qq:789:group") == "789"
    assert raw_group_id("789") == "789"
