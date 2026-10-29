"""_StreamHub 历史保留上限测试（PERF-01/13 配套）。

长思考/长回复时 hub 会保存全部 SSE frame 供重连回放；必须同时受条数与
字符总量约束，避免单会话占用数百 MB。
"""

from easy_agent.api.chat import _HUB_HISTORY_MAX_CHARS, _StreamHub


def test_history_bounded_by_count_and_chars():
    hub = _StreamHub("s1")
    frame = "x" * 1000
    for _ in range(6000):  # 6M 字符 > 4M 上限
        hub.broadcast(frame)

    assert len(hub.history) <= 10000
    assert sum(len(item) for item in hub.history) <= _HUB_HISTORY_MAX_CHARS
    # 保留的是最新事件（尾部），重连仍能拿到收尾内容
    assert hub.history[-1] == frame


def test_small_history_untouched():
    hub = _StreamHub("s1")
    for i in range(10):
        hub.broadcast(f"frame-{i}")
    assert len(hub.history) == 10
    assert hub.history[0] == "frame-0"
