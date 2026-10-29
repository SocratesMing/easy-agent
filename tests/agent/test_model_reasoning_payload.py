"""职责 C：思考内容必须随带 tool_calls 的 assistant 消息回传到出站请求。

langchain_openai 的 ``_convert_message_to_dict`` 不序列化
``additional_kwargs``，思考模式的上游在「工具调用 → 工具结果」续跑时会 400：
``The reasoning_content in the thinking mode must be passed back to the API``。
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


def test_plain_assistant_message_is_not_polluted():
    """无 tool_calls 的 assistant 消息不带 reasoning_content（非思考上游不认该字段）。"""
    messages = [
        HumanMessage(content="你好"),
        AIMessage(content="你好", additional_kwargs={"reasoning_content": "寒暄"}),
    ]

    payload = _model()._get_request_payload(messages)

    assert "reasoning_content" not in payload["messages"][1]


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
