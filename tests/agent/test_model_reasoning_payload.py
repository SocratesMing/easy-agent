"""职责 C：每条 assistant 消息的思考都随出站请求回传（本轮和历史轮次、带不带 tool_calls）。

langchain_openai 的 ``_convert_message_to_dict`` 不序列化
``additional_kwargs``，思考模式的上游在「工具调用 → 工具结果」续跑时会 400：
``The reasoning_content in the thinking mode must be passed back to the API``。
DeepSeek 文档要求请求带 tools 时历史各轮的 ``reasoning_content`` 都回传。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.agent.model import ReasoningChatOpenAI, inject_reasoning_content

TOOL_CALL = {
    "name": "execute",
    "args": {"command": "ls"},
    "id": "call_1",
    "type": "tool_call",
}


def _model() -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(
        model="thinking-model", api_key="k", base_url="http://127.0.0.1:1"
    )


def _tool_loop(reasoning: str = "先列出目录") -> list:
    return [
        HumanMessage(content="列出目录"),
        AIMessage(
            content="",
            tool_calls=[TOOL_CALL],
            additional_kwargs={"reasoning_content": reasoning} if reasoning else {},
        ),
        ToolMessage(content="a.txt", tool_call_id="call_1"),
    ]


def test_payload_carries_reasoning_content_for_tool_call_message():
    payload = _model()._get_request_payload(_tool_loop())

    assistant = payload["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"]["name"] == "execute"
    assert assistant["reasoning_content"] == "先列出目录"


def test_payload_omits_reasoning_content_when_absent():
    payload = _model()._get_request_payload(_tool_loop(reasoning=""))

    assert "reasoning_content" not in payload["messages"][1]


def test_plain_assistant_message_carries_reasoning_content():
    """不带 tool_calls 的回答也回传思考（DeepSeek：即使该轮没有调用工具）。"""
    messages = [
        HumanMessage(content="你好"),
        AIMessage(content="你好", additional_kwargs={"reasoning_content": "寒暄"}),
        HumanMessage(content="再见"),
    ]

    payload = _model()._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == "寒暄"


def test_every_turn_carries_its_reasoning_content():
    """多轮 + 工具调用：历史轮次与本轮的每条 assistant 消息都带各自的思考。"""
    messages = [
        *_tool_loop("第1轮调工具"),
        AIMessage(content="第1轮答复", additional_kwargs={"reasoning_content": "第1轮作答"}),
        HumanMessage(content="第二轮"),
        AIMessage(
            content="",
            tool_calls=[{**TOOL_CALL, "id": "call_2"}],
            additional_kwargs={"reasoning_content": "第2轮调工具"},
        ),
        ToolMessage(content="b.txt", tool_call_id="call_2"),
    ]

    payload = _model()._get_request_payload(messages)

    assistant = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert [m.get("reasoning_content") for m in assistant] == ["第1轮调工具", "第1轮作答", "第2轮调工具"]
    assert all(m.get("reasoning_content") is None for m in payload["messages"] if m["role"] != "assistant")


def test_reasoning_key_aliases_are_normalized_to_reasoning_content():
    messages = [
        HumanMessage(content="列出目录"),
        AIMessage(
            content="",
            tool_calls=[TOOL_CALL],
            additional_kwargs={"reasoning": "别名思考"},
        ),
        ToolMessage(content="a.txt", tool_call_id="call_1"),
    ]

    payload = _model()._get_request_payload(messages)

    assert payload["messages"][1]["reasoning_content"] == "别名思考"


def test_injection_does_not_touch_source_messages():
    messages = _tool_loop()

    payload = _model()._get_request_payload(messages)

    assert "reasoning_content" not in payload["messages"][0]
    assert messages[1].additional_kwargs == {"reasoning_content": "先列出目录"}


def test_inject_reasoning_content_skips_responses_api_payload():
    """Responses API 的输入项带 type，不应塞 chat/completions 的私有字段。"""
    payload = {
        "input": [
            {"type": "message", "role": "assistant", "content": []},
        ]
    }

    inject_reasoning_content(payload, _tool_loop())

    assert "reasoning_content" not in payload["input"][0]


def test_non_stream_response_reasoning_is_extracted():
    """调度任务走 ainvoke（非流式），思考内容同样要落进 additional_kwargs。"""
    response = {
        "id": "r1",
        "model": "thinking-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "先想一想",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "execute", "arguments": '{"command": "ls"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    result = _model()._create_chat_result(response)

    message = result.generations[0].message
    assert message.additional_kwargs["reasoning_content"] == "先想一想"
    assert message.tool_calls[0]["name"] == "execute"
