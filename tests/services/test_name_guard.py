"""专家名统一查重入口（services/experts/name_guard.py）的单元测试。

关注两点：
1. 自定义专家（一次索引 SQL）与预置专家（只读 config.yaml）的查重顺序与短路；
2. 修改场景的 ``exclude_agent_id`` 只作用于自定义专家，避免「和自己重名」。
"""

import pytest

from easy_agent.services.experts import name_guard


class _Store:
    """最小仓储替身：记录 find_by_name_cn 的调用参数。"""

    def __init__(self, conflict=None):
        self.conflict = conflict
        self.calls = []

    def find_by_name_cn(self, user_id, name_cn, exclude_agent_id=""):
        self.calls.append((user_id, name_cn, exclude_agent_id))
        return self.conflict


@pytest.fixture()
def no_preset(monkeypatch):
    """让预置专家查重恒不命中，隔离出自定义专家那一段。"""
    monkeypatch.setattr(
        name_guard, "find_public_expert_by_name_cn", lambda name_cn: None
    )


def test_custom_conflict_short_circuits_preset(no_preset, monkeypatch):
    """自定义专家命中即返回，不再查预置专家（无多余 IO）。"""
    called = []
    monkeypatch.setattr(
        name_guard,
        "find_public_expert_by_name_cn",
        lambda name_cn: called.append(name_cn) or {"scope": "public"},
    )
    store = _Store(conflict={"export_name_cn": "同名专家"})

    hit = name_guard.find_name_conflict(store, "u1", "同名专家")

    assert hit == {"export_name_cn": "同名专家"}
    assert called == []  # 未触发预置查询
    assert store.calls == [("u1", "同名专家", "")]


def test_preset_conflict_checked_after_custom(no_preset, monkeypatch):
    """自定义未命中时再查预置专家，命中返回预置项（带 scope 标记）。"""
    preset = {"export_name_cn": "预置插画师", "scope": "public"}
    monkeypatch.setattr(name_guard, "find_public_expert_by_name_cn", lambda name_cn: preset)
    store = _Store(conflict=None)

    assert name_guard.find_name_conflict(store, "u1", "预置插画师") is preset
    assert store.calls == [("u1", "预置插画师", "")]


def test_exclude_agent_id_passed_to_custom_only(no_preset):
    """修改场景排除自身：exclude_agent_id 原样传给自定义专家查询。"""
    store = _Store(conflict=None)

    assert name_guard.find_name_conflict(
        store, "u1", "插画师", exclude_agent_id="expert_1"
    ) is None
    assert store.calls == [("u1", "插画师", "expert_1")]


def test_no_conflict_returns_none(no_preset):
    assert name_guard.find_name_conflict(_Store(), "u1", "全新专家") is None
