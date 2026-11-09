"""DA-06：把配置的上下文窗口写进 ``model.profile``。

deepagents 按 ``profile["max_input_tokens"]`` 计算默认摘要阈值；OpenAI 兼容网关上的
模型没有内置 profile，框架会退回 170k tokens 的固定阈值。
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from easy_agent.agent.model import apply_context_profile, create_model
from easy_agent.bootstrap.config import Config


def _without_window(profile: dict) -> dict:
    return {k: v for k, v in profile.items() if k != "max_input_tokens"}


def test_configured_context_length_sets_window_and_keeps_capabilities():
    model = ChatOpenAI(model="gpt-4o", api_key="k")
    builtin = dict(model.profile)

    apply_context_profile(model, 64000)

    assert model.profile["max_input_tokens"] == 64000
    assert _without_window(model.profile) == _without_window(builtin)


def test_unconfigured_context_length_keeps_builtin_profile():
    known = ChatOpenAI(model="gpt-4o", api_key="k")
    builtin_window = known.profile["max_input_tokens"]
    apply_context_profile(known, 1_000_000)  # config_models 的缺省值 = 没配置
    assert known.profile["max_input_tokens"] == builtin_window

    gateway = ChatOpenAI(model="my-gateway-model", api_key="k", base_url="http://127.0.0.1:1")
    apply_context_profile(gateway, 1_000_000)
    assert gateway.profile is None


def test_create_model_applies_configured_context_length(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: gw\n"
        "models:\n"
        "  gw:\n"
        "    provider: test\n"
        "    api_key: sk-test\n"
        "    model: my-gateway-model\n"
        "    api_base: http://127.0.0.1:1\n"
        "    context_length: 64000\n",
        encoding="utf-8",
    )
    model = create_model(Config.from_yaml(path))
    assert model.profile["max_input_tokens"] == 64000
