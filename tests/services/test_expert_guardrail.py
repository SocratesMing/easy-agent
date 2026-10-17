"""专家职责边界块（运行时统一追加）单元测试。

覆盖：追加行为、开关关闭、空提示词、防重复拼接。
"""

from easy_agent.services import expert_guardrail as guardrail


def test_append_guardrail_adds_block_after_prompt():
    out = guardrail.append_guardrail("你是量化策略专家。", enabled=True)

    assert out.startswith("你是量化策略专家。")
    assert guardrail.GUARDRAIL_MARKER in out
    # 四条硬约束都在
    assert "领域限定" in out
    assert "相关性检查" in out
    assert "高危操作禁止" in out
    assert "事实与来源" in out
    # 拒答话术与高危操作口径
    assert "请提问我领域内的问题" in out
    assert "重启" in out
    # 边界块在专家提示词之后（recency 更高）
    assert out.index("你是量化策略专家") < out.index(guardrail.GUARDRAIL_MARKER)


def test_append_guardrail_disabled_returns_original():
    text = "你是量化策略专家。"
    assert guardrail.append_guardrail(text, enabled=False) == text


def test_append_guardrail_empty_prompt_untouched():
    """空提示词意味着调用方会降级为通用 Agent，边界块不应单独出现。"""
    assert guardrail.append_guardrail("", enabled=True) == ""
    assert guardrail.append_guardrail(None, enabled=True) == ""


def test_append_guardrail_no_duplicate():
    once = guardrail.append_guardrail("你是专家。", enabled=True)
    twice = guardrail.append_guardrail(once, enabled=True)
    assert twice == once


def test_guardrail_keeps_deliverable_writing_allowed():
    """高危操作禁令不应误伤「在工作区新建交付物」的正常工作方式。"""
    assert "新建" in guardrail.GUARDRAIL_PROMPT and "不受此限" in guardrail.GUARDRAIL_PROMPT


def test_guardrail_strict_scope_rejects_whole_message():
    """严格口径：部分无关也整条拒答（与输入侧预检的 strict 一致）。"""
    out = guardrail.append_guardrail("你是债券专家。", enabled=True, strict=True)

    assert "严格口径" in out
    assert "整条拒答" in out


def test_guardrail_lenient_scope_keeps_partial_answer():
    out = guardrail.append_guardrail("你是债券专家。", enabled=True, strict=False)

    assert "宽松口径" in out
    assert "跳过无关部分即可" in out
    assert "整条拒答" not in out
