"""SummarizationMiddleware 阈值接入测试。

基准是会话所用模型（配置键）的 context_length，不传配置键时是当前默认模型：
- 摘要 trigger/keep 按 compression_threshold / compression_target 折算；
- 工具参数截断同样按该比例折算（官方默认是 ("messages", 20)）。
DA-06 起不再替换框架私有工厂：直接构造官方 ``SummarizationMiddleware``，由
``create_deep_agent`` 按同名原位替换默认项（见 tests/agent/test_summarization_override.py）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from deepagents.middleware.summarization import SummarizationMiddleware

from easy_agent.bootstrap.config import Config
from easy_agent.agent.model import create_model, resolve_context_length
from easy_agent.agent.summarization import build_summarization_middleware, summarization_settings

# 默认模型窗口 1M；会话可在输入框切到窗口 131072 的 small。
# API 模型名（vendor-small-v1）与配置键（small）故意不同：阈值必须按配置键取窗口。
_TWO_MODELS_YAML = """\
model: "big"
models:
  big:
    provider: "p1"
    protocol: "openai"
    model: "vendor-big-v1"
    api_key: "sk-test"
    api_base: "http://127.0.0.1:9/v1"
    context_length: 1000000
  small:
    provider: "p2"
    protocol: "openai"
    model: "vendor-small-v1"
    api_key: "sk-test"
    api_base: "http://127.0.0.1:9/v1"
    context_length: 131072
summarization:
  enabled: true
  compression_threshold: 0.8
  compression_target: 0.1
"""


def _two_model_config(tmp_path) -> Config:
    path = tmp_path / "two-models.yaml"
    path.write_text(_TWO_MODELS_YAML, encoding="utf-8")
    return Config.from_yaml(str(path))


def _expected_thresholds(cfg) -> tuple[tuple, tuple]:
    base = int(cfg.llm.context_length)
    thr = float(cfg.summarization.compression_threshold)
    tgt = float(cfg.summarization.compression_target)
    return ("tokens", max(1, int(base * thr))), ("tokens", max(1, int(base * tgt)))


def test_summarization_settings_use_config_token_thresholds():
    cfg = Config.from_yaml(Config.resolve_config_path())
    exp_trigger, exp_keep = _expected_thresholds(cfg)
    settings = summarization_settings(cfg)

    assert settings["trigger"] == exp_trigger
    assert settings["keep"] == exp_keep
    truncate = settings["truncate_args_settings"]
    assert truncate["trigger"] == exp_trigger
    assert truncate["keep"] == exp_keep
    assert truncate["trigger"] != ("messages", 20)
    assert truncate["max_length"] > 0


def test_build_summarization_middleware_is_the_official_class(monkeypatch):
    """构造官方中间件（名字与框架默认项一致才能原位替换），参数取自 config 阈值。"""
    cfg = Config.from_yaml(Config.resolve_config_path())
    exp_trigger, exp_keep = _expected_thresholds(cfg)
    captured: dict = {}
    original_init = SummarizationMiddleware.__init__

    def _recording_init(self, *args, **kwargs):
        captured.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(SummarizationMiddleware, "__init__", _recording_init)
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from deepagents.backends import StateBackend

    middleware = build_summarization_middleware(cfg, GenericFakeChatModel(messages=iter([])), StateBackend())

    assert isinstance(middleware, SummarizationMiddleware)
    assert middleware.name == "SummarizationMiddleware"
    assert captured["trigger"] == exp_trigger
    assert captured["keep"] == exp_keep


def test_summarization_disabled_raises_thresholds_beyond_reach():
    """enabled=false 时不能"不挂"——框架默认栈仍会挂一份；改为把阈值抬到不可达。"""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        summarization=SimpleNamespace(
            enabled=False,
            compression_threshold=0.8,
            compression_target=0.1,
            truncate_arg_max_length=2000,
        )
    )
    settings = summarization_settings(cfg)

    assert settings["trigger"][0] == "tokens"
    assert settings["trigger"][1] > 10**9
    assert settings["keep"][1] > 10**9
    assert settings["truncate_args_settings"]["trigger"][1] > 10**9


def test_thresholds_follow_the_session_model_key(tmp_path):
    """会话选了非默认模型：阈值按该模型的窗口算，与推给前端的压缩点同一口径。"""
    cfg = _two_model_config(tmp_path)

    settings = summarization_settings(cfg, "small")

    assert settings["trigger"] == ("tokens", int(131072 * 0.8))
    assert settings["keep"] == ("tokens", int(131072 * 0.1))
    assert settings["truncate_args_settings"]["trigger"] == settings["trigger"]
    # orchestrator.resolve_chat_context 推给前端的 auto_compress_tokens 也按配置键取窗口
    assert settings["trigger"][1] == int(resolve_context_length(cfg, "small") * 0.8)
    # 不传配置键才用默认模型
    assert summarization_settings(cfg)["trigger"] == ("tokens", 800000)


def test_build_middleware_takes_the_window_from_the_model_key(tmp_path, monkeypatch, caplog):
    """回归：曾拿 API 模型名（vendor-small-v1）去查配置键，查不到就退回默认模型的 1M 窗口。"""
    import logging

    from deepagents.backends import StateBackend

    caplog.set_level(logging.WARNING, logger="easy_agent.agent.model")
    cfg = _two_model_config(tmp_path)
    captured: dict = {}
    original_init = SummarizationMiddleware.__init__

    def _recording_init(self, *args, **kwargs):
        captured.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(SummarizationMiddleware, "__init__", _recording_init)

    build_summarization_middleware(
        cfg, create_model(cfg, "small"), StateBackend(), model_key="small"
    )

    assert captured["trigger"] == ("tokens", int(131072 * 0.8))
    assert captured["keep"] == ("tokens", int(131072 * 0.1))
    assert "not found in config.models" not in caplog.text
    # 对照：按 API 模型名查会退回默认模型并告警，说明上面那条断言确实能捕获这条日志
    assert resolve_context_length(cfg, "vendor-small-v1") == 1_000_000
    assert "not found in config.models" in caplog.text
