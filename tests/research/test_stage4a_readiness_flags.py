from scripts.close_stage4a_readiness import _strict_bool


def test_readiness_event_flag_parsing_is_strict():
    assert _strict_bool("true") is True
    assert _strict_bool("false") is False
    assert _strict_bool("0") is False
