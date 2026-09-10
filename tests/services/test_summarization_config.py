"""SummarizationMiddleware 阈值接入测试。

基准必须是配置文件里的 ``config.llm.max_input_tokens``：
- 摘要 trigger/keep 按 compression_threshold / compression_target 折算；
- **工具参数截断**同样按该比例折算（默认值是 ("messages", 20)，消息一到 20 条
  就会把历史里 write_file/execute 的参数砍掉，导致下一轮上下文明显缩水）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import deepagents.graph as _graph
from deepagents.middleware.summarization import SummarizationMiddleware

from easy_agent import agent as agent_mod
from easy_agent.config import Config


def _expected_thresholds(cfg) -> tuple[tuple, tuple]:
    base = int(cfg.llm.max_input_tokens)
    thr = float(cfg.summarization.compression_threshold)
    tgt = float(cfg.summarization.compression_target)
    return ("tokens", max(1, int(base * thr))), ("tokens", max(1, int(base * tgt)))


def test_install_config_summarization_folds_fractions_into_config_limit(monkeypatch):
    """trigger/keep/参数截断都应按 config.llm.max_input_tokens 折算。"""
    cfg = Config.from_yaml(Config.resolve_config_path())
    exp_trigger, exp_keep = _expected_thresholds(cfg)

    captured: dict = {}

    def _fake_init(self, *args, **kwargs):  # noqa: ARG001
        captured.update(kwargs)

    # 只记录构造参数，避免真实构造（不依赖模型实例与网络）
    monkeypatch.setattr(SummarizationMiddleware, "__init__", _fake_init)
    # 记录原工厂，测试结束由 monkeypatch 还原，避免污染其它用例
    monkeypatch.setattr(
        _graph, "create_summarization_middleware", _graph.create_summarization_middleware
    )
    monkeypatch.setattr(agent_mod, "_summarization_factory_installed", False)

    agent_mod.install_config_summarization(cfg)
    _graph.create_summarization_middleware(object(), object())

    assert captured["trigger"] == exp_trigger
    assert captured["keep"] == exp_keep

    truncate = captured["truncate_args_settings"]
    assert truncate["trigger"] == exp_trigger
    assert truncate["keep"] == exp_keep
    # 关键：不能退回官方的消息条数阈值
    assert truncate["trigger"] != ("messages", 20)
    assert truncate["max_length"] > 0


def test_install_is_idempotent(monkeypatch):
    """重复调用只替换一次工厂（有全局标志保护）。"""
    cfg = Config.from_yaml(Config.resolve_config_path())

    monkeypatch.setattr(
        _graph, "create_summarization_middleware", _graph.create_summarization_middleware
    )
    monkeypatch.setattr(agent_mod, "_summarization_factory_installed", False)

    agent_mod.install_config_summarization(cfg)
    factory_after_first = _graph.create_summarization_middleware
    agent_mod.install_config_summarization(cfg)  # 第二次应直接 return

    assert _graph.create_summarization_middleware is factory_after_first
    assert agent_mod._summarization_factory_installed is True
