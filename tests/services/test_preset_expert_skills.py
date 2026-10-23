"""预置专家「依赖技能声明」与「技能定位协议」的守护测试。

这两处都容易在后续维护中回退，因此用静态断言兜住：

| # | 断言 | 为什么 |
|---|---|---|
| 1 | ``config.yaml`` 的 ``required_skills`` 必须是与 ``tools.skills_dir`` 逐字一致的 ASCII 目录名 | 目录名是运行时唯一键；写中文 name 虽然能靠别名解析兜底，但改名/多版本时不可控 |
| 2 | 专家提示词/README 不得出现 ``/user-skills/<dir>/SKILL.md`` 兜底路径 | Agent 只扫描 ``/user-skills/`` 扁平目录，写死路径在改名/多层技能后必然失效；正确做法是以技能清单给出的路径为准 |
| 3 | 技能定位协议必须写明「以清单路径为准」与「缺技能报缺」 | 防止改回「自己拼路径」的老写法 |
| 4 | 若本地 ``skills/`` 里真有对应技能包，则声明必须能解析 | 本地放了经营分析两个包，因此这条会真实命中 |
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from easy_agent.services import skill_library as L

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERTS_DIR = REPO_ROOT / "experts"
LOCAL_SKILLS_DIR = REPO_ROOT / "skills"

#: 合法技能目录名：ASCII、可带连字符/下划线
ASCII_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

#: 失效的兜底路径：/user-skills/<目录>/SKILL.md
STALE_USER_SKILL_PATH_RE = re.compile(r"/user-skills/[^/\s`]+/SKILL\.md")


def _expert_configs() -> list[tuple[str, dict]]:
    """返回 [(expert_id, config)]，按目录名排序，保证失败信息可复现。"""
    items: list[tuple[str, dict]] = []
    for config_path in sorted(EXPERTS_DIR.glob("*/v1/config.yaml")):
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        expert_id = str(data.get("expert_agent_id") or config_path.parent.parent.name)
        items.append((expert_id, data))
    return items


def test_experts_exist():
    """守护测试本身要能读到预置专家，否则后面的断言会静默通过。"""
    assert len(_expert_configs()) >= 5


def test_required_skills_are_ascii_dir_names():
    """``required_skills`` 统一写 ASCII 目录名（中文只进 ``skills_names``）。"""
    offending: list[str] = []
    for expert_id, config in _expert_configs():
        for skill in config.get("required_skills") or []:
            value = str(skill).strip()
            if not ASCII_DIR_RE.match(value):
                offending.append(f"{expert_id}: {value!r}")

    assert not offending, (
        "required_skills 必须是 ASCII 技能目录名（与 tools.skills_dir 下的目录一致）；"
        f"以下声明不合规: {offending}"
    )


def test_no_stale_user_skills_fallback_path():
    """专家资产里不得再写 ``/user-skills/<目录>/SKILL.md`` 作为兜底路径。"""
    offenders: list[str] = []
    for path in sorted(EXPERTS_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in STALE_USER_SKILL_PATH_RE.finditer(text):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)}")

    assert not offenders, (
        "提示词里的 /user-skills/<目录>/SKILL.md 兜底路径在目录改名后会失效，"
        f"请改为「以技能清单给出的路径为准」: {offenders}"
    )


def test_skill_protocol_relies_on_listed_path():
    """有技能依赖的专家，定位协议必须写明「以清单路径为准」+「缺技能报缺」。"""
    checked = 0
    problems: list[str] = []
    for expert_id, config in _expert_configs():
        if not (config.get("required_skills") or []):
            continue
        # 提示词正文 = manifest 里登记的文件名；回退取目录下第一个非 README 的 md
        prompt_file = ""
        manifest_path = EXPERTS_DIR / expert_id / "v1" / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                prompt_file = str(manifest.get("prompt_file") or manifest.get("prompt") or "")
            except ValueError:
                prompt_file = ""
        candidates = (
            [EXPERTS_DIR / expert_id / "v1" / prompt_file]
            if prompt_file
            else [
                p
                for p in sorted((EXPERTS_DIR / expert_id / "v1").glob("*.md"))
                if p.name.lower() != "readme.md"
            ]
        )
        for prompt_path in candidates:
            if not prompt_path.is_file():
                continue
            text = prompt_path.read_text(encoding="utf-8", errors="replace")
            checked += 1
            if "技能优先协议" not in text:
                problems.append(f"{expert_id}: 缺少「技能优先协议」章节")
                continue
            if "以该路径为准" not in text and "以清单给出的路径为准" not in text:
                problems.append(f"{expert_id}: 未写明以技能清单给出的路径为准")
            if "报缺" not in text:
                problems.append(f"{expert_id}: 未写明缺技能时报缺")

    assert checked > 0, "没有找到任何带技能依赖的专家提示词"
    assert not problems, problems


def test_declared_skills_resolve_when_pack_present():
    """本地/服务器 ``skills/`` 里真实存在该技能包时，声明必须能解析到目录。

    本地仓库只放基础技能 + 经营分析两个包，业务技能多数不在，因此逐条跳过；
    在装了完整技能包的服务器上跑 CI 时这条才真正生效（能抓住「目录改名但声明没改」）。
    """
    if not LOCAL_SKILLS_DIR.is_dir():
        pytest.skip("本地没有 skills/ 目录")

    checked = 0
    failures: list[str] = []
    for expert_id, config in _expert_configs():
        for skill in config.get("required_skills") or []:
            ref = str(skill).strip()
            if not (LOCAL_SKILLS_DIR / ref).is_dir():
                continue  # 本地没有这个包，无从校验
            checked += 1
            if L.resolve_public_skill_ref(ref, LOCAL_SKILLS_DIR) is None:
                failures.append(f"{expert_id}: {ref}")

    if not checked:
        pytest.skip("本地公共技能目录未包含预置专家的业务技能包")
    assert not failures, f"技能包存在但声明解析失败: {failures}"


def test_local_pack_display_name_resolves_to_own_dir():
    """本地每个技能包：用它的 frontmatter name 解析，必须回到自己的目录。

    这条覆盖「目录英文 + name 中文」的核心场景——只要包被放进 skills/，
    无论声明写名字还是写目录，都能定位到同一个包。
    """
    if not LOCAL_SKILLS_DIR.is_dir():
        pytest.skip("本地没有 skills/ 目录")

    packs = L.iter_skill_dirs(LOCAL_SKILLS_DIR)
    if not packs:
        pytest.skip("本地 skills/ 下没有技能包")

    failures: list[str] = []
    for pack in packs:
        display_name = str(L.skill_metadata(pack).get("name") or pack.name)
        hit = L.resolve_public_skill_ref(display_name, LOCAL_SKILLS_DIR)
        if not hit or hit["dir_name"] != pack.name:
            failures.append(f"{pack.name}: name={display_name!r} -> {hit and hit['dir_name']}")

    assert not failures, failures


def test_display_name_collision_keeps_dir_as_key():
    """本地 ``skills/`` 里若有技能 name 与另一个包目录同名，解析必须优先目录名。

    这条是防御性的：真实技能包改版后容易出现「A 的 name 变成 B 的目录名」，
    此时按 name 解析会静默指向 B，必须让目录名优先。
    """
    if not LOCAL_SKILLS_DIR.is_dir():
        pytest.skip("本地没有 skills/ 目录")

    dir_names = {pack.name for pack in L.iter_skill_dirs(LOCAL_SKILLS_DIR)}
    collisions = [
        (pack.name, str(L.skill_metadata(pack).get("name") or ""))
        for pack in L.iter_skill_dirs(LOCAL_SKILLS_DIR)
        if str(L.skill_metadata(pack).get("name") or "") in dir_names
        and str(L.skill_metadata(pack).get("name") or "") != pack.name
    ]
    if not collisions:
        pytest.skip("本地没有 name 与其它目录同名的技能包")

    failures: list[str] = []
    for pack_dir, display_name in collisions:
        hit = L.resolve_public_skill_ref(display_name, LOCAL_SKILLS_DIR)
        if not hit or hit["dir_name"] != display_name or hit["matched_by"] != "dir":
            failures.append(f"{pack_dir}: name={display_name!r} -> {hit and hit['dir_name']}")

    assert not failures, failures
