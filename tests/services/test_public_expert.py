"""公共（预置）专家目录服务（services/public_expert_service.py）的测试。

覆盖点：

| # | 场景 | 预期 |
|---|---|---|
| 1 | 平铺布局（config.yaml + {name_en}.md） | 被发现，卡片字段与 /agent/list 对齐 |
| 2 | 版本布局（v2 + active=2） | 取 active 指向的版本提示词与版本号 |
| 3 | active 非法（写坏 / 指向不存在的版本） | 回退最大版本 |
| 4 | 目录里没有提示词 md | 跳过（不抛异常），并记 warning |
| 5 | ID 查询 | find_public_expert 命中 / 未命中返回 None |
| 6 | 读取提示词 | load_public_prompt 返回全文与版本；不存在返回空串 |
| 7 | 根目录解析 | 配置 > 环境变量 EASY_EXPERTS_DIR > 默认 <项目根>/experts；相对路径按 CWD→项目根解析 |

用例完全离线：不碰数据库、不调模型，只读写 tmp_path。
"""

from pathlib import Path

import pytest

import easy_agent.services.agent_manager as agent_manager
from easy_agent.services import public_expert_service as P


@pytest.fixture()
def root(tmp_path, monkeypatch):
    """把公共专家根目录指向 tmp_path（清掉 agent 配置，让环境变量生效）。"""
    monkeypatch.setattr(agent_manager, "get_agent_config", lambda: {})
    monkeypatch.setenv(P.ENV_EXPERTS_DIR, str(tmp_path))
    return tmp_path


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _flat_expert(
    root,
    expert_id: str = "preset_demo",
    *,
    name_cn: str = "演示专家",
    name_en: str = "demo-expert",
    version: int = 1,
    tag_id: str = "macro_research",
    prompt: str = "你是「演示专家」，请按结构化方式回答。",
) -> None:
    """写一个平铺布局的公共专家目录。"""
    _write(
        root / expert_id / "config.yaml",
        "\n".join(
            [
                f"expert_agent_id: {expert_id}",
                f"name_cn: {name_cn}",
                f"name_en: {name_en}",
                f"version: {version}",
                f"tag_id: {tag_id}",
                "description: 用于测试的演示专家描述",
                "skills_names: [宏观投研, 利率研判, 资产配置]",
                "features_prompt:",
                "  - 帮我看看这个数据",
                f"prompt_file: {name_en}.md",
                "update_timestamp: '2026-10-14T09:30:00'",
            ]
        ),
    )
    _write(root / expert_id / f"{name_en}.md", prompt)
    _write(root / expert_id / "README.md", "# 说明\n")


# ── 1. 平铺布局 ────────────────────────────────────────────────────────


def test_flat_layout_discovered(root):
    _flat_expert(root)
    items = P.discover_public_experts()

    assert len(items) == 1
    item = items[0]
    # 字段名与 api/expert.py 的 _to_public 对齐（前端卡片/召唤链路可原样复用）
    assert item["export_agent_id"] == "preset_demo"
    assert item["export_name_cn"] == "演示专家"
    assert item["export_name_en"] == "demo-expert"
    assert item["tag_id"] == "macro_research"
    assert item["export_skills_names"] == ["宏观投研", "利率研判", "资产配置"]
    assert item["export_features_prompt"] == ["帮我看看这个数据"]
    assert item["export_version"] == 1
    assert item["agent_status"] == "active"
    assert item["scope"] == "public"
    # 列表不下发提示词全文，保持接口轻量
    assert item["export_prompt"] == ""
    assert item["update_timestamp"] == "2026-10-14T09:30:00"


def test_missing_description_falls_back_to_prompt_first_line(root):
    _flat_expert(root, expert_id="preset_nodec", prompt="你是「无描述专家」。\n第二行。")
    (root / "preset_nodec" / "config.yaml").write_text(
        "expert_agent_id: preset_nodec\nname_cn: 无描述专家\n", encoding="utf-8"
    )
    item = P.discover_public_experts()[0]
    assert item["export_description"].startswith("你是「无描述专家」")


def test_multiple_experts_sorted_by_name(root):
    _flat_expert(root, "preset_b", name_cn="乙专家", name_en="b-expert")
    _flat_expert(root, "preset_a", name_cn="甲专家", name_en="a-expert")
    names = [i["export_name_cn"] for i in P.discover_public_experts()]
    assert names == ["乙专家", "甲专家"]


# ── 2/3. 版本目录布局 ──────────────────────────────────────────────────


def _versioned_expert(root, expert_id: str = "preset_ver", active: str = "2") -> None:
    for version, text in ((1, "v1 提示词"), (2, "v2 提示词")):
        _write(
            root / expert_id / f"v{version}" / "config.yaml",
            f"expert_agent_id: {expert_id}\nname_cn: 版本专家\nversion: {version}\n",
        )
        _write(root / expert_id / f"v{version}" / "expert.md", text)
    _write(root / expert_id / "active", active)


def test_version_layout_uses_active_version(root):
    _versioned_expert(root, active="1")
    item = P.discover_public_experts()[0]
    assert item["export_version"] == 1
    text, version, path = P.load_public_prompt("preset_ver")
    assert (text, version) == ("v1 提示词", 1)
    # 注意：Windows 文件系统大小写不敏感，_find_prompt_file 的 EXPERT.md 兼容分支
    # 可能命中同名小写文件，故只校验文件存在与类型，不比对大小写
    assert Path(path).is_file() and path.lower().endswith(".md")


def test_active_falls_back_to_latest_version_when_invalid(root):
    _versioned_expert(root, active="not-a-number")
    item = P.discover_public_experts()[0]
    assert item["export_version"] == 2
    text, version, _ = P.load_public_prompt("preset_ver")
    assert (text, version) == ("v2 提示词", 2)


def test_version_dirs_without_active_uses_latest(root):
    _versioned_expert(root, active="")
    (root / "preset_ver" / "active").unlink()
    item = P.discover_public_experts()[0]
    assert item["export_version"] == 2


# ── 4. 脏目录容错 ──────────────────────────────────────────────────────


def test_dir_without_prompt_is_skipped(root):
    _write(root / "preset_broken" / "config.yaml", "name_cn: 坏专家\n")
    _write(root / "preset_broken" / "README.md", "# 只有说明\n")
    assert P.discover_public_experts() == []


def test_hidden_and_underscore_dirs_ignored(root):
    _flat_expert(root, ".hidden", name_en="hidden")
    _flat_expert(root, "_template", name_en="template")
    _flat_expert(root, "preset_ok", name_en="ok")
    ids = [i["export_agent_id"] for i in P.discover_public_experts()]
    assert ids == ["preset_ok"]


def test_missing_root_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_manager, "get_agent_config", lambda: {})
    monkeypatch.setenv(P.ENV_EXPERTS_DIR, str(tmp_path / "not-created"))
    assert P.discover_public_experts() == []


# ── 5/6. 查找与读取 ────────────────────────────────────────────────────


def test_find_public_expert_hit_and_miss(root):
    _flat_expert(root, "preset_x", name_en="x-expert")
    assert P.find_public_expert("preset_x")["export_name_cn"] == "演示专家"
    assert P.find_public_expert("nope") is None
    assert P.find_public_expert("") is None


def test_load_public_prompt_missing_expert(root):
    assert P.load_public_prompt("nope") == ("", 0, "")


# ── 7. 根目录解析优先级 ────────────────────────────────────────────────


def test_root_prefers_config_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv(P.ENV_EXPERTS_DIR, str(tmp_path / "from-env"))
    monkeypatch.setattr(
        agent_manager,
        "get_agent_config",
        lambda: {"config": type("C", (), {"tools": type("T", (), {"experts_dir": str(tmp_path / "from-config")})()})()},
    )
    assert P.public_experts_root() == tmp_path / "from-config"


def test_root_env_over_default(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_manager, "get_agent_config", lambda: {})
    monkeypatch.setenv(P.ENV_EXPERTS_DIR, str(tmp_path / "env-dir"))
    assert P.public_experts_root() == tmp_path / "env-dir"


def test_root_default_when_nothing_set(monkeypatch):
    """未配置任何目录时，默认锚定「项目根/experts」，不随进程 CWD 漂移。"""
    monkeypatch.setattr(agent_manager, "get_agent_config", lambda: {})
    monkeypatch.delenv(P.ENV_EXPERTS_DIR, raising=False)

    root, source = P.public_experts_root_info()
    assert source == "default"
    anchored = P.PROJECT_ROOT / P.DEFAULT_EXPERTS_DIRNAME
    if anchored.is_dir():
        assert root == anchored
    else:
        # 非常规布局：退回旧口径（相对 CWD）
        assert root == Path(P.DEFAULT_EXPERTS_DIR)


def test_root_default_is_independent_of_cwd(tmp_path, monkeypatch):
    """换启动目录后仍能找到预置专家（本次改动的核心诉求）。

    用假的「项目根」做断言，避免往真实仓库目录里写测试数据。
    """
    fake_root = tmp_path / "proj"
    fake_root.mkdir()
    _flat_expert(fake_root / P.DEFAULT_EXPERTS_DIRNAME, "preset_cwd", name_en="cwd")

    monkeypatch.setattr(agent_manager, "get_agent_config", lambda: {})
    monkeypatch.setattr(P, "PROJECT_ROOT", fake_root)
    monkeypatch.delenv(P.ENV_EXPERTS_DIR, raising=False)
    monkeypatch.chdir(tmp_path)  # CWD 下没有 experts/

    root, source = P.public_experts_root_info()
    assert source == "default"
    assert root == fake_root / P.DEFAULT_EXPERTS_DIRNAME
    ids = [i["export_agent_id"] for i in P.discover_public_experts()]
    assert ids == ["preset_cwd"]


def test_root_relative_config_resolves_against_project_root(tmp_path, monkeypatch):
    """配置里写相对目录时，CWD 下没有就按项目根找（部署换目录不必改启动方式）。"""
    fake_root = tmp_path / "proj"
    (fake_root / "my-experts").mkdir(parents=True)
    monkeypatch.setattr(P, "PROJECT_ROOT", fake_root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        agent_manager,
        "get_agent_config",
        lambda: {
            "config": type(
                "C", (), {"tools": type("T", (), {"experts_dir": "my-experts"})()}
            )()
        },
    )
    root, source = P.public_experts_root_info()
    assert source == "config"
    assert root == fake_root / "my-experts"


def test_root_missing_dir_keeps_raw_path(tmp_path, monkeypatch):
    """两处都不存在时保留原样（报错信息指向用户真正写下的位置）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        agent_manager,
        "get_agent_config",
        lambda: {
            "config": type(
                "C", (), {"tools": type("T", (), {"experts_dir": "no-such-dir"})()}
            )()
        },
    )
    root, source = P.public_experts_root_info()
    assert source == "config"
    assert root == tmp_path / "no-such-dir"
    assert P.public_experts_root_hint().startswith(str(root.absolute()))


# ── 仓库内置预置专家的结构守门 ──────────────────────────────────────────
# 目的：预置专家是随代码交付的内容资产，其目录结构与提示词结构与「我的专家」
# 保持一致这件事必须由测试兜底，避免后续改动悄悄退化。

REPO_EXPORTS = Path(__file__).resolve().parents[2] / "experts"

#: 提示词 MD 必须包含的五段结构（与 services/expert_service.build_expert_md 对齐）
REQUIRED_SECTIONS = ["## 核心能力", "## 工作流程", "## 输出规范", "## 注意事项"]
#: 语言要求：全链路简体中文（放在文件末尾，recency 最高）
REQUIRED_LANG_MARK = "简体中文"


def _repo_presets() -> list[str]:
    if not REPO_EXPORTS.is_dir():
        return []
    return sorted(p.name for p in REPO_EXPORTS.iterdir() if p.is_dir())


def test_repo_presets_use_version_layout(monkeypatch):
    """每个内置预置专家都是「active + v{n}/」布局，且 active 指向存在的版本目录。"""
    monkeypatch.setattr(P, "public_experts_root", lambda: REPO_EXPORTS)
    names = _repo_presets()
    if not names:
        pytest.skip("仓库未内置预置专家")

    for name in names:
        expert_dir = REPO_EXPORTS / name
        active = expert_dir / "active"
        assert active.is_file(), f"{name} 缺少 active 版本指针"
        assert (expert_dir / f"v{active.read_text(encoding='utf-8').strip()}").is_dir()

    # 全部预置专家都能被服务发现（无脏目录）
    discovered = {it["export_agent_id"] for it in P.discover_public_experts()}
    assert len(discovered) == len(names)

    # 卡片里的提示词文件名与 manifest 记录一致，且 manifest 与 config 关键字段一致
    import json

    import yaml

    for name in names:
        version_dir = REPO_EXPORTS / name / f"v{(REPO_EXPORTS / name / 'active').read_text(encoding='utf-8').strip()}"
        cfg = yaml.safe_load((version_dir / "config.yaml").read_text(encoding="utf-8"))
        manifest = json.loads((version_dir / "manifest.json").read_text(encoding="utf-8"))
        prompt_file = cfg.get("prompt_file")
        assert (version_dir / prompt_file).is_file(), f"{name} 的提示词文件缺失"
        assert manifest["prompt_file"] == prompt_file
        assert manifest["expert_agent_id"] == cfg["expert_agent_id"] == name
        assert manifest["name_en"] == cfg["name_en"]
        assert manifest["version"] == cfg["version"] == int(
            (REPO_EXPORTS / name / "active").read_text(encoding="utf-8").strip()
        )
        # 卡片展示字段：功能描述 ≤ 300 字、技能标签恰好 3 个、示例问题 3~5 条
        assert 0 < len(cfg["description"]) <= 300, f"{name} 的功能描述超长"
        assert len(cfg["skills_names"]) == 3, f"{name} 的技能标签必须为 3 个"
        assert 3 <= len(cfg["features_prompt"]) <= 5, f"{name} 的示例问题应为 3~5 条"


def test_repo_preset_prompts_have_required_structure(monkeypatch):
    """每个预置提示词都含 角色设定 + 核心能力 + 工作流程 + 输出规范 + 注意事项 + 语言要求。"""
    monkeypatch.setattr(P, "public_experts_root", lambda: REPO_EXPORTS)
    names = _repo_presets()
    if not names:
        pytest.skip("仓库未内置预置专家")

    for item in P.discover_public_experts():
        text, _, path = P.load_public_prompt(item["export_agent_id"])
        assert text.strip(), f"{item['export_agent_id']} 提示词为空"
        # frontmatter + 护栏注释 + 标题
        assert text.startswith("---\n"), f"{path} 缺少 YAML frontmatter"
        assert "角色设定" in text, f"{path} 缺少护栏注释"
        for section in REQUIRED_SECTIONS:
            assert section in text, f"{path} 缺少章节 {section}"
        assert REQUIRED_LANG_MARK in text, f"{path} 缺少简体中文输出要求"
        # 语言要求必须在正文最后（追加到系统提示词末尾时约束力最强）
        assert text.index("语言要求") > text.index("## 注意事项"), (
            f"{path} 的「语言要求」应位于「注意事项」之后"
        )
