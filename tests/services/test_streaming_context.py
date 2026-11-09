"""会话历史重建为 LangChain 消息链的测试。"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.services.streaming.messages import build_context_messages


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
    # 没有分步思考块的旧消息：整段思考跟着正文回传
    assert not messages[1].additional_kwargs
    assert messages[4].additional_kwargs == {"reasoning_content": "先查看目录"}
    assert isinstance(messages[5], HumanMessage)
    assert messages[5].content == "第二轮需求"


def test_build_context_messages_restores_thinking_per_step():
    """每个 step 的思考块回到该 step 的 AIMessage，作答那一步的思考跟着正文。"""
    history = [
        {"role": "user", "content": "问题"},
        {
            "role": "assistant",
            "content": "答复",
            "thinking": "想1想2想3",
            "tool_calls": [
                {"tool_name": "ls", "tool_call_id": "c1", "arguments": {}, "result": "a", "step": 1},
                {"tool_name": "ls", "tool_call_id": "c2", "arguments": {}, "result": "b", "step": 2},
            ],
            "blocks": [
                {"type": "thinking", "content": "想1", "step": 1},
                {"type": "tool_call", "tool_call_id": "c1", "step": 1},
                {"type": "thinking", "content": "想2", "step": 2},
                {"type": "tool_call", "tool_call_id": "c2", "step": 2},
                {"type": "thinking", "content": "想3", "step": 3},
                {"type": "content", "content": "答复", "step": 3},
            ],
        },
    ]

    messages = build_context_messages(history, "追问")

    ai = [m for m in messages if isinstance(m, AIMessage)]
    assert [m.tool_calls[0]["id"] if m.tool_calls else m.content for m in ai] == ["c1", "c2", "答复"]
    assert [m.additional_kwargs.get("reasoning_content") for m in ai] == ["想1", "想2", "想3"]


def test_build_context_messages_without_final_step_thinking():
    """思考都属于工具步骤时，正文消息不再重复整段 thinking。"""
    history = [
        {
            "role": "assistant",
            "content": "答复",
            "thinking": "想1",
            "tool_calls": [{"tool_name": "ls", "tool_call_id": "c1", "result": "a", "step": 1}],
            "blocks": [{"type": "thinking", "content": "想1", "step": 1}],
        }
    ]

    messages = build_context_messages(history, "追问")

    assert messages[0].additional_kwargs == {"reasoning_content": "想1"}
    assert messages[2].content == "答复"
    assert not messages[2].additional_kwargs


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


def test_build_context_messages_passes_thinking_back_as_reasoning_content():
    """历史思考进请求的 ``reasoning_content`` 字段（任何 provider），不拼进正文。

    用真实出站载荷断言：glm/minimax 等 provider 曾把思考拼成 ``<think>…</think>`` 文本
    塞进 content，会被当成正文，思考里出现 ``</think>`` 字面量时还会破坏标签配对。
    """
    from easy_agent.agent.model import ReasoningChatOpenAI

    mark = "历史思考标记"
    history = [
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答", "thinking": mark},
    ]
    model = ReasoningChatOpenAI(model="m", api_key="k", base_url="http://127.0.0.1:1")

    for provider in ("deepseek", "glm", "minimax", ""):
        messages = build_context_messages(history, "第二轮问题", provider=provider)
        assistant = model._get_request_payload(messages)["messages"][1]
        assert assistant["reasoning_content"] == mark, provider
        assert assistant["content"] == "第一轮回答"
        assert "think>" not in str(assistant)
