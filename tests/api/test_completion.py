"""公共对话补全接口 /agent/completion/chat 测试。

SEC-09：接口必须登录；消息条数（≤50）、单条长度（≤100000）与内容总长度
（≤100000）都有限制；限流中间件的 ``_DEFAULT_RULES`` 覆盖
``/agent/completion/chat`` 与 legacy ``/api/completion/chat``。

直接调用 chat_completion 处理函数并注入 mock LLM，验证入参消息数组 ->
返回完整上下文数组（含 assistant 回复）的契约，以及参数校验。
不依赖 TestClient lifespan，避免启动重型调度器。
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import easy_agent.services.agents.manager as agent_manager
from easy_agent.api.completion import MAX_TOTAL_CONTENT_CHARS, chat_completion
from easy_agent.middleware.rate_limit import _DEFAULT_RULES
from easy_agent.models.api import ChatCompletionMessage, ChatCompletionRequest

_USERNAME = "testuser"

_CHAT_URL = "/agent/completion/chat"


class _FakeMsg:
    def __init__(self, content, thinking=""):
        self.content = content
        self.additional_kwargs = {"reasoning_content": thinking} if thinking else {}


class _FakeLLM:
    """最小 mock：仅需支持 ainvoke 并返回带 content/additional_kwargs 的消息。"""

    def __init__(self, content="mock-response", thinking=""):
        self._content = content
        self._thinking = thinking
        self.received = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.received = list(messages)
        return _FakeMsg(self._content, self._thinking)


def _req(*pairs):
    return ChatCompletionRequest(
        messages=[ChatCompletionMessage(role=r, content=c) for r, c in pairs]
    )


def _chat(client, messages, **extra):
    return client.post(_CHAT_URL, json={"messages": messages, **extra})


@pytest.fixture()
def fake_llm(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(agent_manager, "_llm_instance", llm)
    return llm


async def test_returns_full_context(fake_llm):
    ctx = await chat_completion(
        _req(("system", "你是翻译助手"), ("user", "把 hello 翻译成中文")),
        username=_USERNAME,
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
    ctx = await chat_completion(_req(("user", "hi")), username=_USERNAME)
    assert ctx[-1]["role"] == "assistant"
    assert ctx[-1]["content"] == "ok"
    assert ctx[-1]["thinking"] == "深思"


async def test_assistant_thinking_is_passed_back_to_the_model(fake_llm):
    """上次返回的 thinking 原样带回：进模型的 AIMessage 带 reasoning_content，返回上下文也保留。"""
    req = ChatCompletionRequest(
        messages=[
            ChatCompletionMessage(role="user", content="q1"),
            ChatCompletionMessage(role="assistant", content="a1", thinking="上一轮思考"),
            ChatCompletionMessage(role="user", content="q2", thinking="用户消息不带思考"),
        ]
    )

    ctx = await chat_completion(req, username=_USERNAME)

    assistant = fake_llm.received[1]
    assert assistant.content == "a1"
    assert assistant.additional_kwargs == {"reasoning_content": "上一轮思考"}
    assert not getattr(fake_llm.received[2], "additional_kwargs", {})
    assert ctx[1] == {"role": "assistant", "content": "a1", "thinking": "上一轮思考"}
    assert ctx[2] == {"role": "user", "content": "q2"}


async def test_thinking_counts_toward_the_total_limit(fake_llm):
    req = ChatCompletionRequest(
        messages=[
            ChatCompletionMessage(role="assistant", content="a", thinking="t" * 60_000),
            ChatCompletionMessage(role="user", content="u" * 60_000),
        ]
    )
    with pytest.raises(HTTPException) as exc:
        await chat_completion(req, username=_USERNAME)
    assert exc.value.status_code == 400
    assert fake_llm.received == []


async def test_empty_messages_raises(fake_llm):
    with pytest.raises(HTTPException) as exc:
        await chat_completion(ChatCompletionRequest(messages=[]), username=_USERNAME)
    assert exc.value.status_code == 400


async def test_invalid_role_raises(fake_llm):
    with pytest.raises(HTTPException) as exc:
        await chat_completion(_req(("developer", "hi")), username=_USERNAME)
    assert exc.value.status_code == 400


async def test_llm_not_initialized(monkeypatch):
    monkeypatch.setattr(agent_manager, "_llm_instance", None)
    with pytest.raises(HTTPException) as exc:
        await chat_completion(_req(("user", "hi")), username=_USERNAME)
    assert exc.value.status_code == 503


async def test_total_content_limit_rejected_before_llm(fake_llm):
    """总长度超限 → 400，且不调用大模型（SEC-09）。"""
    req = _req(("user", "x" * 60_000), ("user", "y" * 60_000))
    with pytest.raises(HTTPException) as exc:
        await chat_completion(req, username=_USERNAME)
    assert exc.value.status_code == 400
    assert "总长度" in exc.value.detail


def test_message_count_limit_rejected_by_model():
    with pytest.raises(ValidationError):
        ChatCompletionRequest(
            messages=[ChatCompletionMessage(role="user", content="hi")] * 51
        )


# ── 端点级：鉴权 / 输入上限 / 限流规则 ────────────────────────────────


def test_endpoint_requires_auth(auth_client):
    """未携带有效凭证 → 401（SEC-09）。"""
    resp = _chat(auth_client, [{"role": "user", "content": "hi"}])
    assert resp.status_code == 401


def test_endpoint_rejects_too_many_messages(client):
    resp = _chat(client, [{"role": "user", "content": "hi"}] * 51)
    assert resp.status_code == 422


def test_endpoint_rejects_single_message_too_long(client):
    resp = _chat(
        client,
        [{"role": "user", "content": "x" * (MAX_TOTAL_CONTENT_CHARS + 1)}],
    )
    assert resp.status_code == 422


def test_endpoint_rejects_oversized_total_content(client):
    resp = _chat(client, [{"role": "user", "content": "x" * 20_000}] * 6)
    assert resp.status_code == 400
    assert "总长度" in resp.json()["detail"]


def test_endpoint_returns_context_when_authenticated(client, monkeypatch):
    monkeypatch.setattr(agent_manager, "_llm_instance", _FakeLLM())
    resp = _chat(client, [{"role": "user", "content": "hi"}])
    assert resp.status_code == 200
    body = resp.json()
    assert body[-1]["role"] == "assistant"
    assert body[-1]["content"] == "mock-response"


def test_completion_endpoints_are_rate_limited():
    """SEC-09：两条路径（/agent 与 legacy /api）都在默认限流规则里。"""
    rules = {prefix: (limit, window) for prefix, limit, window in _DEFAULT_RULES}
    assert rules["/agent/completion/chat"] == (30, 60.0)
    assert rules["/api/completion/chat"] == (30, 60.0)
