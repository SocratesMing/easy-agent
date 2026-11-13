"""编排器与后台驱动共享的运行结果，不依赖 SSE 文本。"""

from dataclasses import dataclass


@dataclass(slots=True)
class StreamOutcome:
    """每次执行独立创建；已转换为事件的失败也必须留在结果中。

    正常耗尽且 error 为空才算成功；取消与租约丢失由后台驱动优先判定。
    """

    error: str = ""

    def fail(self, error: str) -> None:
        self.error = error or "处理失败"
