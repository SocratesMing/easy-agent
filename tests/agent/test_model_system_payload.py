"""职责 D：出站请求里 system 消息的 ``content`` 一律是字符串。

deepagents 0.7 的中间件每次往系统提示追加一个 ``{"type": "text"}`` 块，langchain_openai
把块数组原样序列化，system 的 ``content`` 就成了数组；string 是所有 OpenAI 兼容接口都接受的形状。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from easy_agent.agent.model import ReasoningChatOpenAI, flatten_system_content


def _model() -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(
        model="thinking-model", api_key="k", base_url="http://127.0.0.1:1"
    )


def test_system_text_blocks_become_one_string():
    system = SystemMessage(
        content=[
            {"type": "text", "text": "你是 Easy Agent。"},
            {"type": "text", "text": "\n\n## `write_todos`"},
            {"type": "text", "text": "\n\n## Skills System"},
            {"type": "text", "text": "\n\n<agent_memory>记忆</agent_memory>"},
        ]
    )

    payload = _model()._get_request_payload([system, HumanMessage(content="你好")])

    assert payload["messages"][0] == {
        "role": "system",
        "content": "你是 Easy Agent。\n\n## `write_todos`\n\n## Skills System\n\n<agent_memory>记忆</agent_memory>",
    }


def test_string_system_content_is_unchanged():
    payload = _model()._get_request_payload(
        [SystemMessage(content="你是助手"), HumanMessage(content="你好")]
    )

    assert payload["messages"][0]["content"] == "你是助手"


def test_only_system_messages_are_flattened():
    user_blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    payload = _model()._get_request_payload(
        [
            SystemMessage(content=[{"type": "text", "text": "sys"}]),
            HumanMessage(content=user_blocks),
        ]
    )

    assert payload["messages"][0]["content"] == "sys"
    assert payload["messages"][1]["content"] == user_blocks


def test_system_with_non_text_block_is_left_as_is():
    content = [{"type": "text", "text": "sys"}, {"type": "image_url", "image_url": {"url": "x"}}]
    payload = {"messages": [{"role": "system", "content": list(content)}]}

    flatten_system_content(payload)

    assert payload["messages"][0]["content"] == content


def test_flattening_keeps_reasoning_passback():
    messages = [
        SystemMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "\n\nb"}]),
        HumanMessage(content="列出目录"),
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "ls"}, "id": "call_1", "type": "tool_call"}],
            additional_kwargs={"reasoning_content": "先列出目录"},
        ),
        ToolMessage(content="a.txt", tool_call_id="call_1"),
    ]

    payload = _model()._get_request_payload(messages)

    assert payload["messages"][0]["content"] == "a\n\nb"
    assert payload["messages"][2]["reasoning_content"] == "先列出目录"
