from easy_agent.initialization.logging import _ROTATED_LOG_SUFFIX


def test_rotated_log_suffix_is_a_compiled_fullmatch_pattern():
    # 按天轮转（midnight）
    assert _ROTATED_LOG_SUFFIX.fullmatch("2026-09-09") is not None
    # 大小触发轮转：带时刻后缀
    assert _ROTATED_LOG_SUFFIX.fullmatch("2026-09-09_11-22-33") is not None
    # 同秒多文件的序号后缀
    assert _ROTATED_LOG_SUFFIX.fullmatch("2026-09-09.1") is not None
    # 无关文件不得误删
    assert _ROTATED_LOG_SUFFIX.fullmatch("2026-09-09.extra") is None
