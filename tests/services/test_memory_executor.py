"""记忆更新线程池隔离测试（PERF-16）。"""

from easy_agent.services import streaming


def test_memory_updates_use_dedicated_bounded_executor():
    executor = streaming._MEMORY_UPDATE_EXECUTOR
    assert executor._max_workers == 2
    assert executor._thread_name_prefix == "memory-update"
