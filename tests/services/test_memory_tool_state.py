"""记忆落盘前的「工具 / 环境能力状态」检测（log-only）。

背景：模型会把「本轮没有 web_search / 未开启联网 / 沙箱禁 shell」写进记忆，而记忆
每轮重新注入，下一轮就不再核对实际工具清单——表现为「明明开着联网，却回答没有网络」。
提示词（memory_update.md / long_term_memory.md）负责让它别写，这里守住兜底检测：
现阶段只打日志不删内容（正则误删会伤用户画像），确认无漏判误判后再改为剔除。

因此本文件锁两件事：
1. 真实污染样本必须被检出（含「元规则 + 能力快照」同行的情形）；
2. 正常用户画像不能被误判（「我在 Windows 上用 shell 做数据清洗」这类）。
"""

from easy_agent.services.memory.manager import (
    find_tool_state_clauses,
    log_tool_state_findings,
)

#: 线上真实出现的污染片段（memories/<user>/AGENTS.md 摘要）
POLLUTED = """# 用户长期记忆

## 工作背景

- 环境：无行情源及Wind/中债/akshare；联网以本轮实际注入工具清单为准，每次作答前核对，
  曾误判"未联网"被纠正，不得凭记忆默认无网；不可shell/脚本抓外网；Windows沙箱禁shell。

## 决策与理由

- 如果工具清单里没有 `web_search`，就不用 shell 或脚本抓取外网来代替。
- 应明确告知本轮无法联网，并请用户开启联网搜索。
"""

#: 正常用户画像：提到 shell / 联网但是陈述背景与偏好，不是能力快照
CLEAN = """# 用户长期记忆

## 工作背景

- 16 年利率债投研，负责组合久期与曲线策略。
- 我在 Windows 上用 shell 做数据清洗，偏好 pandas。
- 工具可用性以本轮实际注入的清单为准，不依据记忆判断。
- 偏好在结论前先给数据来源，格式用表格。
"""


def test_detects_real_pollution():
    hits = find_tool_state_clauses(POLLUTED)
    joined = "\n".join(hits)
    assert "无行情源" in joined
    assert "不可shell" in joined
    assert "沙箱禁shell" in joined
    assert "没有 `web_search`" in joined
    assert "无法联网" in joined


def test_meta_rule_clause_is_kept_out_of_hits():
    """「联网以本轮实际注入工具清单为准」是正确写法，不能被判成污染。"""
    # 同一行里既有元规则又有能力快照：元规则子句不判，快照子句判——整行判会漏。
    hits = find_tool_state_clauses(POLLUTED)
    assert all("以本轮实际注入" not in h for h in hits)
    assert hits, "同一行里的能力快照子句必须被检出"


def test_clean_profile_has_no_hit():
    assert find_tool_state_clauses(CLEAN) == []


def test_empty_and_heading_only_are_safe():
    assert find_tool_state_clauses("") == []
    assert find_tool_state_clauses("# 用户长期记忆\n\n## 工作背景\n") == []


def test_log_findings_does_not_raise(monkeypatch):
    """log-only：只写日志，绝不改动文本。"""
    calls = []
    monkeypatch.setattr(
        "easy_agent.services.memory.manager.logger.warning",
        lambda msg, *a, **kw: calls.append(msg),
    )
    log_tool_state_findings(POLLUTED, scope="用户长期")
    assert calls and "工具/环境状态" in calls[0]
    # 无命中时不产生日志噪声
    calls.clear()
    log_tool_state_findings(CLEAN, scope="用户长期")
    assert calls == []
