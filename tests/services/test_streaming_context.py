"""会话历史重建为 LangChain 消息链的测试。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.services.streaming import build_context_messages


def test_build_context_messages_restores_tool_calls_and_results():
    history = [
        {"role": "user", "content": "第一轮需求"},
        {
            "role": "assistant",
            "content": "已完成",
            "thinking": "先查看目录",
            "tool_calls": [
                {
                    "tool_name": "ls",
                    "tool_call_id": "call-1",
                    "arguments": {"path": "/workspace"},
                    "result": "file.txt",
                    "success": True,
                    "duration": 0.1,
                    "step": 1,
                },
                {
                    "tool_name": "read_file",
                    "tool_call_id": "call-2",
                    "arguments": {"file_path": "/workspace/file.txt"},
                    "result": "hello",
                    "success": True,
                    "duration": 0.2,
                    "step": 1,
                },
            ],
        },
    ]

    messages = build_context_messages(history, "第二轮需求", provider="deepseek")

    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "第一轮需求"
    assert isinstance(messages[1], AIMessage)
    assert [
        (tool_call["name"], tool_call["args"], tool_call["id"])
        for tool_call in messages[1].tool_calls
    ] == [
        ("ls", {"path": "/workspace"}, "call-1"),
        ("read_file", {"file_path": "/workspace/file.txt"}, "call-2"),
    ]
    assert isinstance(messages[2], ToolMessage)
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].name == "ls"
    assert messages[2].content == "file.txt"
    assert isinstance(messages[3], ToolMessage)
    assert messages[3].tool_call_id == "call-2"
    assert messages[3].content == "hello"
    assert isinstance(messages[4], AIMessage)
    assert messages[4].content == "已完成"
    # 历史思考不回灌上下文（deepseek 通道下请求侧也会丢弃 reasoning_content）
    assert not messages[4].additional_kwargs
    assert isinstance(messages[5], HumanMessage)
    assert messages[5].content == "第二轮需求"


def test_build_context_messages_supports_blocks_without_tool_calls():
    history = [
        {
            "role": "assistant",
            "content": "已完成",
            "blocks": [
                {
                    "type": "tool_call",
                    "tool_name": "execute",
                    "tool_call_id": "call-3",
                    "arguments": {"command": "pwd"},
                    "result": "/workspace",
                }
            ],
        }
    ]

    messages = build_context_messages(history, "继续")

    assert isinstance(messages[0], AIMessage)
    assert messages[0].tool_calls[0]["name"] == "execute"
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].content == "/workspace"
    assert isinstance(messages[2], AIMessage)
    assert messages[2].content == "已完成"
    assert isinstance(messages[3], HumanMessage)


def test_build_context_messages_keeps_plain_history_compatible():
    history = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]

    messages = build_context_messages(history, "追问")

    assert [type(message) for message in messages] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]


def test_build_context_messages_never_reinjects_thinking():
    """历史思考内容不得进入请求 payload（任何 provider）。

    用真实序列化结果断言，而不只看 additional_kwargs：
    ark/glm 等 provider 曾把思考拼成 ``<think>…</think>`` 文本塞进 content，
    会重复占用 token 且可能干扰模型。
    """
    import json

    from langchain_openai.chat_models.base import _convert_message_to_dict

    mark = "历史思考标记_DO_NOT_SEND"
    history = [
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答", "thinking": mark},
    ]

    for provider in ("deepseek", "ark", "glm", "minimax", ""):
        messages = build_context_messages(history, "第二轮问题", provider=provider)
        payload = [_convert_message_to_dict(m) for m in messages]
        dumped = json.dumps(payload, ensure_ascii=False)
        assert mark not in dumped, (provider, dumped)
        assert messages[1].content == "第一轮回答"
        assert "think>" not in dumped
