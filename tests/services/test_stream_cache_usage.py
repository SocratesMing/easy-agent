"""前缀缓存命中统计（只记日志）：用来核对「记忆每轮改写会不会让历史前缀缓存失效」。"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, AIMessageChunk

from easy_agent.agent.model import ReasoningChatOpenAI, _fill_cache_read_from_deepseek_usage
from easy_agent.services.streaming.processor import StreamProcessor, describe_cache_usage


def _ai(content="", *, inp, cached=None, tool=False):
    usage = {"input_tokens": inp, "output_tokens": 5, "total_tokens": inp + 5}
    if cached is not None:
        usage["input_token_details"] = {"cache_read": cached}
    calls = [{"name": "ls", "args": {}, "id": "tc1"}] if tool else []
    return AIMessage(content=content, tool_calls=calls, usage_metadata=usage)


def test_processor_records_cached_input_tokens_per_model_call(caplog):
    p = StreamProcessor(sid="s1")
    with caplog.at_level(logging.INFO, logger="easy_agent.services.streaming.processor"):
        events = p.handle("updates", {"model": {"messages": [_ai(inp=100, cached=80, tool=True)]}})
        p._last_token_usage_time = 0.0  # 绕开 0.3s 节流
        p.handle("updates", {"model": {"messages": [_ai("done", inp=120)]}})
        p.finalize(session_id="s", elapsed_time=0.1)

    assert p.cache_usage == [(100, 80), (120, 0)]
    assert "缓存命中:80(" in caplog.text
    # 只记日志：token_usage 事件与落库字段不变
    token_usage = next(e for e in events if e["type"] == "token_usage")
    assert "cached_tokens" not in token_usage
    assert "cached_tokens" not in p.last_usage


def test_describe_cache_usage_highlights_the_first_call_of_the_turn():
    line = describe_cache_usage([(1000, 200), (1100, 1000)])

    assert line.startswith("首次调用缓存命中 200/1000 (20%)")
    assert "全轮 1200/2100 (57%)" in line and "调用 2 次" in line
    assert describe_cache_usage([]) == ""
    assert describe_cache_usage(None) == ""


def test_deepseek_native_cache_hits_are_mapped_to_cache_read():
    """DeepSeek 官方接口用 prompt_cache_hit_tokens 报命中数，LangChain 不认，这里补上。"""
    model = ReasoningChatOpenAI(model="m", api_key="sk-not-real", base_url="http://127.0.0.1:1")
    chunk = {
        "id": "c1",
        "choices": [],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "total_tokens": 105,
            "prompt_cache_hit_tokens": 64,
            "prompt_cache_miss_tokens": 36,
        },
    }

    gen = model._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, {})

    assert gen.message.usage_metadata["input_token_details"]["cache_read"] == 64


def test_openai_style_cached_tokens_take_precedence():
    msg = AIMessageChunk(
        content="",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 5,
            "total_tokens": 105,
            "input_token_details": {"cache_read": 50},
        },
    )

    _fill_cache_read_from_deepseek_usage(msg, {"prompt_cache_hit_tokens": 64})

    assert msg.usage_metadata["input_token_details"]["cache_read"] == 50
