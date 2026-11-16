"""主代理的技能段用项目模板（``agent/skills_middleware.py``），不用框架通用模板。

框架默认段在本项目里有几处不对：没有技能时引导往只读的 ``/skills/builtin/`` 里建技能、
"Sources labeled Deepagents / Agents" 说的是框架 CLI 的来源、示例里的 web-research 技能不存在。
"""

from __future__ import annotations

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from easy_agent.agent.skills_middleware import NO_SKILLS_TEXT, EasySkillsMiddleware


class _SystemPrompts(BaseChatModel):
    prompts: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "system-prompts"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(str(messages[0].content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])


async def _system_prompt(tmp_path, *, project_template: bool) -> str:
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    model = _SystemPrompts()
    middleware = [EasySkillsMiddleware(backend=backend, sources=["/skills/"])] if project_template else []
    agent = create_deep_agent(model=model, backend=backend, skills=["/skills/"], middleware=middleware)
    await agent.ainvoke({"messages": [HumanMessage("q")]})
    return model.prompts[0]


def _write_skill(tmp_path, name: str, description: str) -> None:
    skill_dir = tmp_path / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n", encoding="utf-8"
    )


async def test_skills_section_uses_project_template(tmp_path):
    _write_skill(tmp_path, "demo", "DEMO-SKILL-DESC")

    prompt = await _system_prompt(tmp_path, project_template=True)

    assert "## 技能" in prompt
    assert "DEMO-SKILL-DESC" in prompt and "/skills/demo/SKILL.md" in prompt
    assert "/skills/builtin/` 只读" in prompt
    assert "Skills System" not in prompt
    assert "web-research" not in prompt
    assert 'Sources labeled "Deepagents"' not in prompt


async def test_empty_skill_list_does_not_invite_writing_to_readonly_dirs(tmp_path):
    (tmp_path / "skills").mkdir()

    prompt = await _system_prompt(tmp_path, project_template=True)

    assert NO_SKILLS_TEXT in prompt
    assert "You can create skills" not in prompt


async def test_framework_template_is_still_the_generic_one(tmp_path):
    """对照：框架默认段。上游改掉这些通用文案后，这条会失败，届时可重新评估覆盖。"""
    (tmp_path / "skills").mkdir()

    prompt = await _system_prompt(tmp_path, project_template=False)

    assert "You can create skills" in prompt
    assert "web-research" in prompt


def test_main_agent_always_uses_project_skills_middleware(build_easy_agent, compiled_agents):
    """没有技能门控工具时也要替换框架默认项，否则技能段又回到通用模板。"""
    build_easy_agent()

    main = next(call for call in compiled_agents if call["name"] == "easy-agent")
    skills = [m for m in main["middleware"] if m.name == "SkillsMiddleware"]
    assert len(skills) == 1 and isinstance(skills[0], EasySkillsMiddleware)


def test_subagents_use_project_skills_middleware_too(build_easy_agent, compiled_agents):
    """子代理（general-purpose / office-writer …）的技能段同样不能回到框架通用模板。"""
    build_easy_agent()

    subagents = [call for call in compiled_agents if call["name"] != "easy-agent"]
    assert subagents
    for call in subagents:
        skills = [m for m in call["middleware"] if m.name == "SkillsMiddleware"]
        assert len(skills) == 1 and isinstance(skills[0], EasySkillsMiddleware), call["name"]
