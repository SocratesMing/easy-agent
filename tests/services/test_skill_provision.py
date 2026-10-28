"""专家依赖技能自动补装（services/skill_provision.py）的测试。

DA-13 S2 语义：补装 = 往 ``workspace/{u}/builtin-skills.json`` 记一条，**不复制技能包**。
覆盖点：

| # | 场景 | 预期 |
|---|---|---|
| 1 | 技能已在公共目录、用户未添加 | 写入清单 enabled=true，返回 installed |
| 2 | 已添加且启用 | 不重复写，记 existing |
| 3 | 已添加但被用户停用 | 不自动装回，记 disabled |
| 4 | 用户技能库有同名技能（fork） | 视为已可用，不动清单 |
| 5 | 公共技能目录里没有该技能包 | fail-soft：记 missing，不抛异常且不写清单 |
| 6 | 未配置公共技能目录 | 记 source_unconfigured，不抛异常 |
| 7 | 配置开关关闭 / 专家无声明 / 无 expert_id | 无动作 |
| 8 | 补装成功后有缓存驱逐调用 | invalidate_user_agents 被调用一次 |
| 9 | 运行时提示 | 缺技能时给出原因段；全部可用时为空串 |
| 10 | 排查接口用的只读状态 | 不写清单 |

用例完全离线：不碰数据库、不调模型，只读写 tmp_path。
"""

from pathlib import Path

import pytest

import easy_agent.services.agent_manager as agent_manager
from easy_agent.agent import builtin_skills
from easy_agent.bootstrap.config import Config
from easy_agent.services import skill_provision as S


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离出「公共技能目录 + 用户工作区」，并关掉真实配置与缓存驱逐。"""
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(agent_manager, "get_agent_config", lambda: {})
    monkeypatch.setattr(builtin_skills, "builtin_root", lambda config=None: skills_root)
    monkeypatch.setattr(
        Config, "get_user_workspace_dir", staticmethod(lambda username, config=None: workspace)
    )
    monkeypatch.setattr(agent_manager, "invalidate_user_agents", lambda username: 1)
    return {"skills": skills_root, "workspace": workspace, "username": "alice"}


def _make_skill(root: Path, name: str, display_name: str | None = None) -> Path:
    """建技能目录。``display_name`` 用于模拟「目录英文 + SKILL.md name 中文」的存量包。"""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter_name = display_name if display_name is not None else name
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {frontmatter_name}\ndescription: 测试技能 {frontmatter_name}\n---\n\n正文\n",
        encoding="utf-8",
    )
    return skill_dir


def _declare(monkeypatch, skills: list[str]) -> None:
    monkeypatch.setattr(S, "required_skills_of", lambda expert_id: list(skills))


def _store(env) -> dict:
    return builtin_skills.load_store(env["username"])


# ── 补装成功路径 ──────────────────────────────────────────────────────


def test_installs_required_skill_without_copying(env, monkeypatch):
    _make_skill(env["skills"], "factor-creator")
    _declare(monkeypatch, ["factor-creator"])

    report = S.provision_expert_skills(env["username"], "preset_factor_research")

    assert report["installed"] == ["factor-creator"]
    assert report["skipped"] == []
    assert report["cache_invalidated"] == 1
    # 清单登记 enabled=true；技能包本体仍只在公共目录
    assert _store(env)["skills"]["factor-creator"]["enabled"] is True
    assert not (env["workspace"] / "skills" / "factor-creator").exists()
    assert (env["skills"] / "factor-creator" / "SKILL.md").is_file()


def test_already_added_is_not_rewritten(env, monkeypatch):
    _make_skill(env["skills"], "pm-research")
    builtin_skills.add(env["username"], "pm-research")
    added_at = _store(env)["skills"]["pm-research"]["added_at"]
    _declare(monkeypatch, ["pm-research"])

    report = S.provision_expert_skills(env["username"], "preset_gold_investment")

    assert report["installed"] == []
    assert report["existing"] == ["pm-research"]
    assert report["cache_invalidated"] == 0
    assert _store(env)["skills"]["pm-research"]["added_at"] == added_at


def test_disabled_skill_is_not_reinstalled(env, monkeypatch):
    _make_skill(env["skills"], "strategy-pm")
    builtin_skills.add(env["username"], "strategy-pm")
    builtin_skills.set_enabled(env["username"], "strategy-pm", False)
    _declare(monkeypatch, ["strategy-pm"])

    report = S.provision_expert_skills(env["username"], "preset_strategy_research")

    assert report["installed"] == []
    assert report["skipped"][0]["reason"] == "disabled"
    # 用户停用是主动意图，不能被补装悄悄打开
    assert _store(env)["skills"]["strategy-pm"]["enabled"] is False


def test_user_skill_fork_counts_as_available(env, monkeypatch):
    _make_skill(env["workspace"] / "skills", "dc-research")
    _declare(monkeypatch, ["dc-research"])

    report = S.provision_expert_skills(env["username"], "preset_rate_bond")

    assert report["existing"] == ["dc-research"]
    assert report["installed"] == []
    assert "dc-research" not in _store(env)["skills"]


# ── 别名：required_skills 写 SKILL.md 的 name（中文）──────────────────


def test_declared_by_display_name_installs_canonical_dir(env, monkeypatch):
    """存量包「目录英文 + name 中文」：声明写中文名也能装上，清单键是目录名。"""
    _make_skill(env["skills"], "fpp-income-analysis", "金市营收情况")
    _make_skill(env["skills"], "precious-metals-analysis", "贵金属投资交易分析")
    _declare(monkeypatch, ["金市营收情况", "贵金属投资交易分析"])

    report = S.provision_expert_skills(env["username"], "preset_business_analysis")

    # 装的是 canonical 目录名，不是声明里的中文名
    assert report["installed"] == ["fpp-income-analysis", "precious-metals-analysis"]
    assert set(_store(env)["skills"]) == {"fpp-income-analysis", "precious-metals-analysis"}
    # resolved 记录声明 → 目录的映射，供排查接口使用
    assert report["resolved"] == [
        {"declared": "金市营收情况", "resolved_dir": "fpp-income-analysis", "matched_by": "name"},
        {
            "declared": "贵金属投资交易分析",
            "resolved_dir": "precious-metals-analysis",
            "matched_by": "name",
        },
    ]
    assert report["cache_invalidated"] == 1


def test_display_name_and_dir_name_both_declared_installs_once(env, monkeypatch):
    """同一技能用两种写法各声明一次：只装一次，第二次记 existing，不重复写清单。"""
    _make_skill(env["skills"], "fpp-income-analysis", "金市营收情况")
    _declare(monkeypatch, ["金市营收情况", "fpp-income-analysis"])

    report = S.provision_expert_skills(env["username"], "preset_business_analysis")

    assert report["installed"] == ["fpp-income-analysis"]
    assert report["existing"] == ["fpp-income-analysis"]
    assert list(_store(env)["skills"]) == ["fpp-income-analysis"]


def test_normalized_dir_name_declaration_resolves(env, monkeypatch):
    """归一化写法（大小写/下划线）也能命中：FPP_Income_Analysis → fpp-income-analysis。"""
    _make_skill(env["skills"], "fpp-income-analysis", "金市营收情况")
    _declare(monkeypatch, ["FPP_Income_Analysis"])

    report = S.provision_expert_skills(env["username"], "preset_business_analysis")

    assert report["installed"] == ["fpp-income-analysis"]
    assert report["resolved"][0]["matched_by"] == "dir_normalized"


def test_unresolvable_declaration_reports_candidates(env, monkeypatch):
    """解析不到的声明：fail-soft，附近似候选（仅提示，不自动装）。"""
    _make_skill(env["skills"], "fpp-income-analysis", "金市营收情况")
    _declare(monkeypatch, ["fpp_income_analysi"])  # 少了结尾的 s

    report = S.provision_expert_skills(env["username"], "preset_business_analysis")

    assert report["missing"] == ["fpp_income_analysi"]
    skipped = report["skipped"][0]
    assert skipped["reason"] == "source_missing"
    assert skipped["candidates"][0]["dir_name"] == "fpp-income-analysis"
    # 候选只用于诊断，绝不据此安装
    assert _store(env)["skills"] == {}


def test_status_exposes_resolution_fields(env, monkeypatch):
    """排查接口的只读状态：声明/目录/命中方式都能看到。"""
    _make_skill(env["skills"], "fpp-income-analysis", "金市营收情况")
    _declare(monkeypatch, ["金市营收情况"])

    status = S.required_skills_status(env["username"], "preset_business_analysis")

    item = status["items"][0]
    assert item["declared"] == "金市营收情况"
    assert item["resolved_dir"] == "fpp-income-analysis"
    assert item["matched_by"] == "name"
    assert item["state"] == "installed_ready"
    assert _store(env)["skills"] == {}  # 只读，不写清单


# ── fail-soft 路径 ────────────────────────────────────────────────────


def test_missing_skill_package_does_not_raise(env, monkeypatch):
    _declare(monkeypatch, ["macro-research", "factor-creator"])
    _make_skill(env["skills"], "factor-creator")

    report = S.provision_expert_skills(env["username"], "preset_strategy_research")

    assert report["missing"] == ["macro-research"]
    assert report["installed"] == ["factor-creator"]
    reasons = {item["skill"]: item["reason"] for item in report["skipped"]}
    assert reasons["macro-research"] == "source_missing"
    assert "macro-research" not in _store(env)["skills"]


def test_unconfigured_skills_root_does_not_raise(env, monkeypatch):
    monkeypatch.setattr(builtin_skills, "builtin_root", lambda config=None: None)
    _declare(monkeypatch, ["pm-research"])

    report = S.provision_expert_skills(env["username"], "preset_gold_investment")

    assert report["skills_root"] == ""
    assert report["skipped"][0]["reason"] == "source_unconfigured"


# ── 无动作路径 ────────────────────────────────────────────────────────


def test_switch_off_disables_provisioning(env, monkeypatch):
    _make_skill(env["skills"], "factor-creator")
    _declare(monkeypatch, ["factor-creator"])
    monkeypatch.setattr(S, "_auto_install_enabled", lambda: False)

    report = S.provision_expert_skills(env["username"], "preset_factor_research")

    assert report["enabled"] is False
    assert report["installed"] == []
    assert _store(env)["skills"] == {}


@pytest.mark.parametrize(
    "expert_id,skills",
    [(None, ["factor-creator"]), ("", ["factor-creator"]), ("preset_mine", [])],
)
def test_no_expert_or_no_declaration_is_noop(env, monkeypatch, expert_id, skills):
    _make_skill(env["skills"], "factor-creator")
    _declare(monkeypatch, skills)

    report = S.provision_expert_skills(env["username"], expert_id)

    assert report["installed"] == []
    assert report["required"] == []
    assert _store(env)["skills"] == {}


# ── 运行时提示与排查状态 ──────────────────────────────────────────────


def test_runtime_notice_mentions_missing_skill(env, monkeypatch):
    _declare(monkeypatch, ["pm-research"])
    report = S.provision_expert_skills(env["username"], "preset_gold_investment")

    notice = S.build_skill_runtime_notice(report)

    assert "pm-research" in notice
    assert "服务端公共技能目录中没有这个技能包" in notice
    assert "不要虚构技能产物" in notice


def test_runtime_notice_empty_when_all_available(env, monkeypatch):
    _make_skill(env["skills"], "pm-research")
    _declare(monkeypatch, ["pm-research"])
    report = S.provision_expert_skills(env["username"], "preset_gold_investment")

    assert S.build_skill_runtime_notice(report) == ""


def test_status_is_read_only(env, monkeypatch):
    _make_skill(env["skills"], "factor-creator")
    _declare(monkeypatch, ["factor-creator", "missing-one"])

    status = S.required_skills_status(env["username"], "preset_factor_research")

    assert [item["state"] for item in status["items"]] == ["installed_ready", "source_missing"]
    assert status["skills_root"] == str(env["skills"])
    # 只读接口不能有副作用
    assert _store(env)["skills"] == {}


def test_required_skills_of_reads_public_config(env, monkeypatch, tmp_path):
    """required_skills_of 走 public_expert_service（这里用假实现验证接线）。"""
    from easy_agent.services import public_expert_service

    monkeypatch.setattr(
        public_expert_service, "required_skills_of_public_expert", lambda expert_id: ["a", "b"]
    )
    assert S.required_skills_of("preset_x") == ["a", "b"]
    assert S.required_skills_of(None) == []
