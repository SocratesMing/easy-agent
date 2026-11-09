"""主代理与子代理的技能中间件：框架 ``SkillsMiddleware`` + 项目自己的技能段提示词。

框架默认的技能段（``SKILLS_SYSTEM_PROMPT``）是通用模板，放进本项目有几处不对：

- 没有技能时提示 "You can create skills in /skills/builtin/ or /user-skills/"，但公共技能目录只读；
- "Sources labeled Deepagents / Agents" 说的是框架 CLI 的技能来源，本项目没有这两类；
- 示例里的 web-research 技能不存在。

这里换成中文模板，其余行为（技能发现、``include_tools`` 门控、pin）全部沿用框架实现。
``name`` 与框架默认项相同，经 ``create_deep_agent(middleware=...)`` 原位替换默认实例；
子代理经 spec 的 ``middleware`` 同样替换（``agent/subagents.py:apply_skill_gated_tools``）。
"""

from __future__ import annotations

from deepagents.middleware.skills import SkillsMiddleware

from easy_agent.agent.builtin_skills import BUILTIN_VIRTUAL_ROOT

#: 技能段模板（替换框架的 ``SKILLS_SYSTEM_PROMPT``）。框架用 ``str.format`` 填入三个槽位，
#: 正文里不能再出现其他花括号（f-string 里写成双花括号）。
SKILLS_PROMPT = f"""## 技能

技能库提供特定任务的操作规范、脚本与参考资料。

{{skills_locations}}{{skills_load_warnings}}

**可用技能：**

{{skills_list}}

**使用方式（按需加载）：**

1. 用户的任务和某个技能的描述对得上时，先用 `read_file` 读取清单里给出的 SKILL.md 路径，传 `limit=1000`（默认只读 100 行，不够）。
2. 按 SKILL.md 里的步骤执行；技能目录里的脚本、配置和参考文档都用清单给出的绝对路径访问。
3. 没有对得上的技能时直接完成任务，不要硬套技能。
4. 公共技能目录 `{BUILTIN_VIRTUAL_ROOT}/` 只读，不要往里写文件。"""

#: 没有可用技能时的清单文字（框架默认文案会引导往只读的公共技能目录里建技能）。
NO_SKILLS_TEXT = "（暂无可用技能）"


class EasySkillsMiddleware(SkillsMiddleware):
    """框架技能中间件，换上项目的技能段模板与「暂无技能」文案。"""

    def __init__(self, *, backend, sources, system_prompt: str | None = SKILLS_PROMPT, tools=None) -> None:
        super().__init__(backend=backend, sources=sources, system_prompt=system_prompt, tools=tools)

    @property
    def name(self) -> str:
        return "SkillsMiddleware"

    def _format_skills_list(self, skills) -> str:
        # 只改空清单的文案；有技能时沿用框架格式（含 allowed_tools 与 SKILL.md 路径）。
        # 框架改名或改签名时 tests/agent/test_skills_prompt.py 会失败。
        if not skills:
            return NO_SKILLS_TEXT
        return super()._format_skills_list(skills)
