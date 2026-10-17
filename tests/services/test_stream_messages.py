"""stream_messages 纯函数测试：工具记录累积、历史消息重建、持久化字典。

对应 ARCH-03 Phase 3 的纯逻辑抽取；不依赖数据库/网络/真实 LLM。
"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.services.stream_messages import (
    _historical_tool_records,
    _partial_message_dict,
    _tool_call_tuples,
    build_assistant_message_dict,
    build_context_messages,
    tool_call_records_to_dicts,
)


def test_streaming_reexports_same_function_objects():
    """既有 ``from ...streaming import X`` 导入路径必须保持指向同一实现。"""
    from easy_agent.services import streaming

    assert streaming.build_context_messages is build_context_messages
    assert streaming.build_assistant_message_dict is build_assistant_message_dict
    assert streaming.tool_call_records_to_dicts is tool_call_records_to_dicts
    assert streaming._partial_message_dict is _partial_message_dict
    assert streaming._historical_tool_records is _historical_tool_records
    assert streaming._tool_call_tuples is _tool_call_tuples


def test_tool_call_records_to_dicts_full_tuple_and_approval():
    records = [
        ("ls", "call-1", {"path": "/w"}, "ok", True, 0.1, 2, "approved"),
    ]

    [record] = tool_call_records_to_dicts(records)

    assert record == {
        "tool_name": "ls",
        "tool_call_id": "call-1",
        "arguments": {"path": "/w"},
        "result": "ok",
        "success": True,
        "duration": 0.1,
        "step": 2,
        "approval_status": "approved",
    }


def test_tool_call_records_to_dicts_legacy_seven_tuple_defaults_to_none():
    [record] = tool_call_records_to_dicts(
        [("ls", "call-1", {}, "ok", True, 0.1, 1)]
    )

    assert record["approval_status"] is None


def test_tool_call_records_to_dicts_truncates_result():
    long_text = "x" * 100
    [untruncated] = tool_call_records_to_dicts(
        [("t", "c", {}, long_text, True, 0, 1)]
    )
    [truncated] = tool_call_records_to_dicts(
        [("t", "c", {}, long_text, True, 0, 1)], result_limit=4
    )
    [non_str] = tool_call_records_to_dicts(
        [("t", "c", {}, 12345, True, 0, 1)], result_limit=3
    )

    assert untruncated["result"] == long_text
    assert truncated["result"] == "xxxx"
    assert non_str["result"] == "123"


def test_historical_tool_records_prefers_tool_calls_then_blocks():
    preferred = {
        "tool_calls": [{"tool_name": "ls"}, "not-a-dict"],
        "blocks": [{"type": "tool_call", "tool_name": "ignored"}],
    }
    fallback = {
        "tool_calls": [],
        "blocks": [
            {"type": "tool_call", "tool_name": "read_file"},
            {"type": "content", "content": "plain"},
            "not-a-dict",
        ],
    }

    assert _historical_tool_records(preferred) == [{"tool_name": "ls"}]
    assert _historical_tool_records(fallback) == [
        {"type": "tool_call", "tool_name": "read_file"}
    ]
    assert _historical_tool_records({}) == []


def test_tool_call_tuples_extracts_only_tool_call_blocks():
    blocks = [
        {"type": "thinking", "content": "t", "step": 1},
        {
            "type": "tool_call",
            "tool_name": "ls",
            "tool_call_id": "c1",
            "arguments": {"path": "/w"},
            "result": "a.txt",
            "success": True,
            "duration": 0.2,
            "step": 1,
            "approval_status": "approved",
        },
        {"type": "tool_call"},
    ]

    plain = _tool_call_tuples(blocks)
    with_approval = _tool_call_tuples(blocks, with_approval=True)

    assert plain == [
        ("ls", "c1", {"path": "/w"}, "a.txt", True, 0.2, 1),
        ("", "", {}, "", True, None, 0),
    ]
    assert with_approval == [
        ("ls", "c1", {"path": "/w"}, "a.txt", True, 0.2, 1, "approved"),
        ("", "", {}, "", True, None, 0, None),
    ]


def test_build_context_messages_falls_back_to_blocks_for_legacy_history():
    """旧会话只有 ``blocks`` 没有 ``tool_calls`` 时仍能按 step 重建工具链。"""
    history = [
        {"role": "system", "content": "ignored"},
        {
            "role": "assistant",
            "content": "   ",
            "blocks": [
                {
                    "type": "tool_call",
                    "tool_name": "ls",
                    "tool_call_id": "b1",
                    "arguments": {"path": "/w"},
                    "result": "a.txt",
                    "step": 1,
                },
                {
                    "type": "tool_call",
                    "tool_name": "read_file",
                    "arguments": {"file_path": "a.txt"},
                    "result": "hi",
                    "step": 2,
                },
                {"type": "tool_call"},
            ],
        },
    ]

    messages = build_context_messages(history, "下一轮")

    # step 1 / step 2 / step 0（缺省字段块）各生成一组 AIMessage + ToolMessage
    assert [type(m) for m in messages] == [
        AIMessage,
        ToolMessage,
        AIMessage,
        ToolMessage,
        AIMessage,
        ToolMessage,
        HumanMessage,
    ]
    assert [
        (tc["name"], tc["args"], tc["id"]) for tc in messages[0].tool_calls
    ] == [("ls", {"path": "/w"}, "b1")]
    assert messages[1].content == "a.txt"
    assert messages[1].tool_call_id == "b1"
    assert messages[2].tool_calls[0]["id"] == "historical-1"
    assert messages[3].content == "hi"
    # 缺省字段兜底：tool_name -> "tool"，tool_call_id -> historical-<index>
    assert [
        (tc["name"], tc["args"], tc["id"]) for tc in messages[4].tool_calls
    ] == [("tool", {}, "historical-2")]
    assert messages[5].content == ""
    # 纯空白 content 不追加 assistant 正文消息
    assert messages[-1].content == "下一轮"


def test_build_context_messages_keeps_thinking_only_assistant_turn():
    history = [{"role": "assistant", "content": "", "thinking": "只思考没正文"}]

    messages = build_context_messages(history, "q")

    assert isinstance(messages[0], AIMessage)
    assert messages[0].content == ""
    assert not messages[0].additional_kwargs
    assert isinstance(messages[1], HumanMessage)


def test_build_assistant_message_dict_cleans_blocks_and_usage():
    message = build_assistant_message_dict(
        content="   ",
        thinking="",
        thinking_duration=0,
        tool_call_records=[("ls", "c1", {}, "x" * 10, True, 1.5, 2)],
        blocks=[
            {"type": "content", "content": "   "},
            {"type": "thinking", "content": "t", "step": 1},
        ],
        input_tokens=12,
        output_tokens=3,
        context_tokens=0,
        elapsed_time=1.234,
        step_count=2,
        result_limit=4,
    )

    assert message["role"] == "assistant"
    assert message["content"] == ""
    assert message["thinking"] is None
    assert message["thinking_duration"] is None
    assert message["tool_calls"][0]["result"] == "xxxx"
    assert message["blocks"] == [{"type": "thinking", "content": "t", "step": 1}]
    assert message["usage"] == {
        "input_tokens": 12,
        "output_tokens": 3,
        "reasoning_tokens": 0,
        "context_tokens": 12,  # context_tokens<=0 时回退 input_tokens
        "elapsed_time": 1.23,
        "step_count": 2,
    }
    assert isinstance(message["timestamp"], str)


def test_build_assistant_message_dict_empty_records_become_none():
    message = build_assistant_message_dict(
        content="answer",
        thinking="think",
        thinking_duration=1.0,
        tool_call_records=[],
        blocks=[],
        input_tokens=1,
        output_tokens=1,
        context_tokens=1,
        elapsed_time=0.5,
        step_count=1,
        reasoning_tokens=7,
    )

    assert message["tool_calls"] is None
    assert message["blocks"] is None
    assert message["thinking"] == "think"
    assert message["usage"]["reasoning_tokens"] == 7


def test_partial_message_dict_uses_processor_state_and_approval_flag():
    proc = SimpleNamespace(
        accumulated_response="answer",
        accumulated_thinking="think",
        blocks=[
            {"type": "thinking", "content": "think", "step": 1},
            {
                "type": "tool_call",
                "tool_name": "rm",
                "tool_call_id": "c1",
                "arguments": {"path": "a.txt"},
                "result": "blocked",
                "success": False,
                "duration": 0.1,
                "step": 1,
                "approval_status": "pending",
            },
        ],
        last_usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "reasoning_tokens": 2,
        },
        last_context_tokens=0,
        current_step=1,
    )

    partial = _partial_message_dict(proc, 1.234)
    with_approval = _partial_message_dict(proc, 1.234, with_approval=True)

    assert partial["content"] == "answer"
    assert partial["thinking"] == "think"
    assert partial["tool_calls"][0]["approval_status"] is None
    assert partial["usage"]["context_tokens"] == 10
    assert partial["usage"]["reasoning_tokens"] == 2
    assert partial["usage"]["elapsed_time"] == 1.23
    assert with_approval["tool_calls"][0]["approval_status"] == "pending"
