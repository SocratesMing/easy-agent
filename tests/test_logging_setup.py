from easy_agent.app import _ROTATED_LOG_SUFFIX


def test_rotated_log_suffix_is_a_compiled_fullmatch_pattern():
    assert _ROTATED_LOG_SUFFIX.fullmatch(".2026-09-09") is not None
    assert _ROTATED_LOG_SUFFIX.fullmatch(".2026-09-09.extra") is None
