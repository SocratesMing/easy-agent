"""SummarizationMiddleware 阈值接入测试。

基准是配置里当前模型的 context_length：
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
from easy_agent.agent.summarization import build_summarization_middleware, summarization_settings


def _expected_thresholds(cfg) -> tuple[tuple, tuple]:
    base = int(cfg.llm.context_length)
    thr = float(cfg.summarization.compression_threshold)
    tgt = float(cfg.summarization.compression_target)
    return ("tokens", max(1, int(base * thr))), ("tokens", max(1, int(base * tgt)))


def test_summarization_settings_use_config_token_thresholds():
    cfg = Config.from_yaml(Config.resolve_config_path())
    exp_trigger, exp_keep = _expected_thresholds(cfg)
    settings = summarization_settings(cfg, object())

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
    settings = summarization_settings(cfg, object())

    assert settings["trigger"][0] == "tokens"
    assert settings["trigger"][1] > 10**9
    assert settings["keep"][1] > 10**9
    assert settings["truncate_args_settings"]["trigger"][1] > 10**9
