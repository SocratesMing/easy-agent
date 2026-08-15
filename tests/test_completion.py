"""公共对话补全接口 /api/completion/chat 测试。

直接调用 chat_completion 处理函数并注入 mock LLM，验证入参消息数组 ->
返回完整上下文数组（含 assistant 回复）的契约，以及参数校验。
不依赖 TestClient lifespan，避免启动重型调度器。
"""

import pytest
from fastapi import HTTPException

import easy_agent.services.agent_manager as agent_manager
from easy_agent.api.completion import chat_completion
from easy_agent.models.api import ChatCompletionRequest, ChatCompletionMessage


class _FakeMsg:
    def __init__(self, content, thinking=""):
        self.content = content
        self.additional_kwargs = {"reasoning_content": thinking} if thinking else {}


class _FakeLLM:
    """最小 mock：仅需支持 ainvoke 并返回带 content/additional_kwargs 的消息。"""

    def __init__(self, content="mock-response", thinking=""):
        self._content = content
        self._thinking = thinking

    async def ainvoke(self, messages, *args, **kwargs):
        return _FakeMsg(self._content, self._thinking)


def _req(*pairs):
    return ChatCompletionRequest(
        messages=[ChatCompletionMessage(role=r, content=c) for r, c in pairs]
    )


@pytest.fixture()
def fake_llm(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(agent_manager, "_llm_instance", llm)
    return llm


async def test_returns_full_context(fake_llm):
    ctx = await chat_completion(
        _req(("system", "你是翻译助手"), ("user", "把 hello 翻译成中文"))
    )
    assert isinstance(ctx, list)
    assert len(ctx) == 3
    assert ctx[0] == {"role": "system", "content": "你是翻译助手"}
    assert ctx[1] == {"role": "user", "content": "把 hello 翻译成中文"}
    assert ctx[2]["role"] == "assistant"
    assert ctx[2]["content"] == "mock-response"


async def test_includes_thinking_when_present(monkeypatch):
    monkeypatch.setattr(
        agent_manager, "_llm_instance", _FakeLLM(content="ok", thinking="深思")
    )
    ctx = await chat_completion(_req(("user", "hi")))
    assert ctx[-1]["role"] == "assistant"
    assert ctx[-1]["content"] == "ok"
    assert ctx[-1]["thinking"] == "深思"


async def test_empty_messages_raises(fake_llm):
    with pytest.raises(HTTPException) as exc:
        await chat_completion(ChatCompletionRequest(messages=[]))
    assert exc.value.status_code == 400


async def test_invalid_role_raises(fake_llm):
    with pytest.raises(HTTPException) as exc:
        await chat_completion(_req(("developer", "hi")))
    assert exc.value.status_code == 400


async def test_llm_not_initialized(monkeypatch):
    monkeypatch.setattr(agent_manager, "_llm_instance", None)
    with pytest.raises(HTTPException) as exc:
        await chat_completion(_req(("user", "hi")))
    assert exc.value.status_code == 503
