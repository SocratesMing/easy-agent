"""模型请求头透传 + ``{session_id}`` 占位符展开的回归测试。

背景：OpenCode Go 网关强制要求 ``x-opencode-session`` 请求头（缺失直接返回
400 MissingSessionID），并要求客户端声明自己的 User-Agent。配置通过
``models.<key>.headers`` 声明这些头，值里的 ``{session_id}`` 在 ``create_model``
时替换为当前会话标识（没有会话上下文时退化为进程内稳定值）。

这些用例不发起网络请求，只验证客户端构造后的请求头。
"""

from easy_agent.config import Config, LLMConfig, ProviderConfig, RetryConfig
from easy_agent.model import build_request_headers, create_model, resolve_llm_config

API_BASE = "https://opencode.ai/zen/go/v1"


def _config(headers: dict[str, str]) -> Config:
    provider = ProviderConfig(
        provider="opencode",
        api_key="test-key",
        model="glm-5.3",
        api_base=API_BASE,
        context_length=200000,
        protocol="openai",
        headers=headers,
    )
    llm = LLMConfig(
        api_key="test-key",
        api_base=API_BASE,
        model="glm-5.3",
        provider="opencode",
        context_length=200000,
        protocol="openai",
        headers=headers,
        retry=RetryConfig(),
    )
    return Config.model_construct(
        llm=llm, models={"opencode": provider}, active_model="opencode"
    )


def test_session_placeholder_is_replaced_with_real_session_id():
    cfg = _config(
        {"User-Agent": "easy-agent/1.0", "x-opencode-session": "{session_id}"}
    )

    llm = create_model(cfg, "opencode", session_id="sess-abc")

    assert llm.default_headers["x-opencode-session"] == "sess-abc"
    assert llm.default_headers["User-Agent"] == "easy-agent/1.0"


def test_session_placeholder_falls_back_to_process_id():
    """没有会话上下文时（如启动自检）仍要带一个稳定值，否则网关 400。"""
    cfg = _config({"x-opencode-session": "{session_id}"})

    llm = create_model(cfg, "opencode")

    assert llm.default_headers["x-opencode-session"].startswith("easy-agent-")


def test_without_headers_behaviour_is_unchanged():
    cfg = _config({})

    assert build_request_headers(cfg.models["opencode"]) is None
    llm = create_model(cfg, "opencode", session_id="sess-abc")
    assert not getattr(llm, "default_headers", None)


def test_resolve_llm_config_passes_headers_through():
    cfg = _config({"x-opencode-session": "{session_id}"})

    resolved = resolve_llm_config(cfg, "opencode")

    assert resolved.headers == {"x-opencode-session": "{session_id}"}
