"""超限异常的对外文案：业务方能看懂，日志仍保留原始异常。"""

from __future__ import annotations

from easy_agent.services.streaming.orchestrator import (
    LIMIT_EXCEEDED_MESSAGE,
    friendly_error_message,
)


def test_limit_exceeded_uses_readable_message():
    from langchain.agents.middleware.model_call_limit import (
        ModelCallLimitExceededError,
    )

    exc = ModelCallLimitExceededError(
        thread_count=0, run_count=100, thread_limit=None, run_limit=100
    )
    # 原始文本仍是给工程看的那句
    assert "run limit (100/100)" in str(exc)
    assert friendly_error_message(exc) == "执行步数用尽，请缩小分析范围或明确数据位置"
    assert friendly_error_message(exc) == LIMIT_EXCEEDED_MESSAGE


def test_other_errors_keep_original_text():
    assert friendly_error_message(RuntimeError("boom")) == "boom"
    assert friendly_error_message(ValueError("bad input")) == "bad input"


def test_limit_message_mentions_step_budget():
    # 文案要能指导用户下一步，不能只说「失败」
    assert "步数" in LIMIT_EXCEEDED_MESSAGE
    assert "缩小" in LIMIT_EXCEEDED_MESSAGE
