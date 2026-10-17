"""日志文件按大小轮转的回归测试（PERF-17：不得每条 flush+fstat 但必须仍能轮转）。"""

import logging

from easy_agent.initialization.log import _TimeSizeRotatingFileHandler


def test_size_rotation_still_works_and_tracks_bytes(tmp_path):
    log_file = tmp_path / "proc.log"
    handler = _TimeSizeRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=3,
        encoding="utf-8",
        delay=False,
        max_bytes=2000,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test-log-rotation")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for i in range(200):
        logger.info("x" * 80 + f"-{i}")

    rotated = [
        p for p in tmp_path.iterdir()
        if p.name.startswith("proc.log.") and p.name != "proc.log"
    ]
    assert rotated, "超过 maxBytes 后应产生轮转文件"
    assert handler._current_bytes <= 2000 + 200, "字节计数应随写入推进（允许一条消息超出）"

    handler.close()
    logger.handlers = []
