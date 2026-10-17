"""SummarizationMiddleware 阈值接入测试。

基准是配置里当前模型的 context_length：
- 摘要 trigger/keep 按 compression_threshold / compression_target 折算；
- 工具参数截断同样按该比例折算（官方默认是 ("messages", 20)）。
建图期间临时替换官方工厂，退出后还原。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import deepagents.graph as _graph
from deepagents.middleware.summarization import SummarizationMiddleware

from easy_agent.config import Config
from easy_agent.summarization import configured_summarization, summarization_settings


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


def test_configured_summarization_restores_factory(monkeypatch):
    """建图上下文内使用官方 SummarizationMiddleware，退出后工厂还原。"""
    cfg = Config.from_yaml(Config.resolve_config_path())
    exp_trigger, exp_keep = _expected_thresholds(cfg)
    captured: dict = {}
    original = _graph.create_summarization_middleware

    def _fake_init(self, *args, **kwargs):  # noqa: ARG001
        captured.update(kwargs)

    monkeypatch.setattr(SummarizationMiddleware, "__init__", _fake_init)

    with configured_summarization(cfg):
        assert _graph.create_summarization_middleware is not original
        _graph.create_summarization_middleware(object(), object())

    assert _graph.create_summarization_middleware is original
    assert captured["trigger"] == exp_trigger
    assert captured["keep"] == exp_keep
