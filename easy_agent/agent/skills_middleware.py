"""项目技能中间件：沿用框架 SkillsMiddleware，替换中文提示词模板并在清单中附带缺失工具提示。"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from deepagents.middleware.skills import SkillsMiddleware

from easy_agent.agent.builtin_skills import BUILTIN_VIRTUAL_ROOT
from easy_agent.agent.skill_readiness import SkillGap, format_gap_hint

logger = logging.getLogger("easy_agent.agent")

#: 技能段模板（替换框架的 ``SKILLS_SYSTEM_PROMPT``）。框架用 ``str.format`` 填入三个槽位，
#: 正文里不能再出现其他花括号（f-string 里写成双花括号）。
SKILLS_PROMPT = f"""## 技能

技能库提供特定任务的操作规范、脚本与参考资料。

{{skills_locations}}{{skills_load_warnings}}

**可用技能：**

{{skills_list}}

**使用方式（按需加载）：**

1. 用户的任务和某个技能的描述对得上时，先用 `read_file` 读取清单里给出的 SKILL.md 路径，传 `limit=1000`（默认只读 100 行，不够）。**读一次就够了——读取完成即视为该技能已加载**，不要为了「确认」再用相同参数读第二遍；文件被截断时用 `offset` 从已读到的行往下续读，不要从头重读。
2. 按 SKILL.md 里的步骤执行；技能目录里的脚本、配置和参考文档都用清单给出的绝对路径访问。
3. 没有对得上的技能时直接完成任务，不要硬套技能。
4. 公共技能目录 `{BUILTIN_VIRTUAL_ROOT}/` 只读，不要往里写文件。

**取数纪律（技能里没写也照此执行）：**

5. 按「验证集」而不是「全集」取数。SKILL.md 就算要求遍历全部区间 / 渠道 / 合约，也先用一个样本确认口径正确，再逐个取不同目标；**同一个数据目标换参数累计 3 次仍无结果，就判定「该数据不可得」并停止**，不要穷举所有参数组合。某个区间返回 0 行 / 空列表，直接视为该区间无数据并记录下来，不要用同参数或换参数反复验证。
6. 结果自相矛盾（例如声称 N 条却返回 0 行、请求里带的筛选条件在响应里为空）属于数据源异常：停止对该目标的查询，在结论里明确写「数据源异常」以及已尝试的范围，不要靠换参数重试来「确认」。
7. 同一份数据取到一次就够了。重复取回已经拿到的清单 / 时间戳不会让结论更可靠，只会让上下文变脏。
8. **清单里标了 ⛔ / ⚠️ 的技能，其缺失工具就是本会话没接入的数据服务。** 这类技能读完文档也取不到数：**直接告知用户「数据未就位」并说明缺少哪类数据服务**，不要靠重读 SKILL.md、换参数重试或改写工具名去「验证」——那只会把一次无效会话拖成长循环。清单**没有**标注的技能不适用本条：它们的取数工具可能在执行时才出现（技能通过 `include_tools` 声明的工具，读完技能即生效），按技能步骤正常调用即可。"""

#: 没有可用技能时的清单文字（框架默认文案会引导往只读的公共技能目录里建技能）。
NO_SKILLS_TEXT = "（暂无可用技能）"


class EasySkillsMiddleware(SkillsMiddleware):
    """框架技能中间件，换上项目的技能段模板与「暂无技能」文案。

    ``tool_gaps`` 是编译期算好的「技能声明的取数工具 − 本会话可用工具」差集
    （``agent/skill_readiness.py``），按技能**目录名**索引；渲染清单时追加到对应
    技能下面，让模型在第一眼就知道该技能的数据通道是否就位。
    """

    def __init__(
        self,
        *,
        backend,
        sources,
        system_prompt: str | None = SKILLS_PROMPT,
        tools=None,
        tool_gaps: dict[str, SkillGap] | None = None,
    ) -> None:
        super().__init__(backend=backend, sources=sources, system_prompt=system_prompt, tools=tools)
        self.tool_gaps = dict(tool_gaps or {})

    @property
    def name(self) -> str:
        return "SkillsMiddleware"

    @staticmethod
    def _skill_dir_name(skill) -> str:
        """技能目录名（``.../fpp-income-analysis/SKILL.md`` → ``fpp-income-analysis``）。"""
        try:
            return PurePosixPath(str(skill.get("path") or "")).parent.name
        except Exception:  # noqa: BLE001 - 取不到目录名只是少一条提示
            return ""

    def _format_skills_list(self, skills) -> str:
        # 只改空清单的文案；有技能时沿用框架格式（含 allowed_tools 与 SKILL.md 路径）。
        # 框架改名或改签名时 tests/agent/test_skills_prompt.py 会失败。
        if not skills:
            return NO_SKILLS_TEXT
        if not self.tool_gaps:
            return super()._format_skills_list(skills)

        # 逐技能渲染再拼接：这样"数据通道未接入"的提示紧跟在对应技能下面，
        # 而不是攒在清单末尾（错位后模型会把提示算到别的技能头上）。
        # 单技能调用父类实现 = 与框架格式逐字一致；无差集时拼回原样。
        blocks: list[str] = []
        for skill in skills:
            block = super()._format_skills_list([skill])
            gap = self.tool_gaps.get(self._skill_dir_name(skill))
            if gap is not None and gap.missing:
                block += "\n" + format_gap_hint(gap)
                logger.debug(
                    "技能数据通道预检：%s %s（缺失: %s）",
                    self._skill_dir_name(skill),
                    gap.summary(),
                    "、".join(gap.missing),
                )
            blocks.append(block)
        return "\n".join(blocks)
