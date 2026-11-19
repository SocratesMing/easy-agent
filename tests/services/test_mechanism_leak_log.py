"""最终答复的「内部机制词」检测（services/streaming/persistence.py）。

2026-11-19 事故：守卫收口后模型把提示里的"已移除全部工具"原样转述给用户
（"金市营收情况的数据查询工具已被系统移除"），把业务结论挤掉了。这里只做
**检测告警**、不改写正文（正文已流式下发，改写会让实时视图与刷新后的历史不一致）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from easy_agent.services.streaming.persistence import _warn_mechanism_leak  # noqa: E402


def test_warns_on_mechanism_phrase_without_printing_body(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_mechanism_leak(
            "sid-1", {"content": "很抱歉，金市营收情况的数据查询工具已被系统移除。"}
        )
    assert "最终答复含内部机制词" in caplog.text
    assert "工具已被系统移除" in caplog.text  # 命中词
    # 正文本身（含业务信息）不进日志
    assert "很抱歉" not in caplog.text


def test_no_warning_for_business_sentence(caplog):
    """收口文案里给模型抄的业务句式不能被当成机制词——否则每次收口都误报。"""
    with caplog.at_level(logging.WARNING):
        _warn_mechanism_leak(
            "sid-2", {"content": "所需的数据服务当前未接入，暂时无法取数。"}
        )
    assert "最终答复含内部机制词" not in caplog.text


def test_detects_phrase_in_content_blocks(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_mechanism_leak(
            "sid-3",
            {"content": [{"type": "text", "text": "重复读取已拦截，请直接给结论"}]},
        )
    assert "最终答复含内部机制词" in caplog.text


def test_survives_odd_payloads(caplog):
    for payload in ({"content": None}, {}, {"content": 123}, {"content": [None]}):
        _warn_mechanism_leak("sid-4", payload)  # 不抛异常即可
