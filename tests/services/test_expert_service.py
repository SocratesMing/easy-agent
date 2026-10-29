"""expert_service 的单元测试：LLM 产物解析、标识生成、版本目录、清理。

全部离线执行：LLM 用假对象注入，不发起任何网络请求。
"""

import json

import pytest

import easy_agent.services.expert_service as esvc
from easy_agent.bootstrap.config import Config


# ── 夹具 ──────────────────────────────────────────────────────────────


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    """把 Config.get_user_workspace_dir 重定向到临时目录，避免污染仓库。"""

    def _dir(username, config=None):
        path = tmp_path / "workspace" / esvc.slugify(username)
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(Config, "get_user_workspace_dir", staticmethod(_dir))
    return tmp_path / "workspace"


@pytest.fixture()
def prompts_dir(tmp_path, monkeypatch):
    """把提示词目录重定向到临时目录。"""
    d = tmp_path / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(esvc, "get_prompts_dir", lambda: d)
    return d


class FakeLLM:
    """假模型：按顺序返回预设内容，并记录调用次数。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        if not self.responses:
            raise RuntimeError("no more canned responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item

        class _Msg:
            content = item

        return _Msg()


VALID_PAYLOAD = {
    "scenario": "用于量化选股与因子研究",
    "capabilities": ["因子挖掘", "回测验证", "风险归因"],
    "workflow": ["明确目标", "构造因子", "回测", "给出结论"],
    "boundaries": ["不编造未提供的行情数据"],
    "system_prompt": "你是量化策略专家，擅长因子选股……",
    "features_prompt": ["帮我评估这组因子的有效性", "帮我设计一个多因子模型"],
    "description": "面向量化投研场景，完成因子挖掘、回测验证与风险归因，输出可复用的策略结论。",
    "skills_names": ["因子分析", "策略回测", "风险控制"],
    "tag_id": "factor_analysis",
}

TAG_OPTIONS = [
    {"tag_id": "macro_research", "tag_name": "宏观投研"},
    {"tag_id": "factor_analysis", "tag_name": "因子分析"},
]

VALID_JSON = json.dumps(VALID_PAYLOAD, ensure_ascii=False)


# ── JSON 提取 ─────────────────────────────────────────────────────────


def test_extract_json_plain():
    assert esvc.extract_json_object(VALID_JSON)["scenario"] == VALID_PAYLOAD["scenario"]


def test_extract_json_with_fence():
    assert esvc.extract_json_object(f"```json\n{VALID_JSON}\n```")["system_prompt"]


def test_extract_json_with_prose_around():
    text = f"好的，以下是配置：\n{VALID_JSON}\n希望有帮助。"
    assert esvc.extract_json_object(text)["scenario"]


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        esvc.extract_json_object("这不是 JSON")
    with pytest.raises(ValueError):
        esvc.extract_json_object("")


# ── 产物规整 ──────────────────────────────────────────────────────────


def test_normalize_config_ok():
    cfg = esvc.normalize_config(VALID_PAYLOAD)
    assert cfg["scenario"]
    assert cfg["capabilities"] == ["因子挖掘", "回测验证", "风险归因"]
    assert cfg["system_prompt"].startswith("你是量化策略专家")
    assert cfg["description"].startswith("面向量化投研场景")
    assert cfg["skills_names"] == ["因子分析", "策略回测", "风险控制"]
    assert cfg["tag_id"] == "factor_analysis"


def test_normalize_config_requires_system_prompt():
    with pytest.raises(ValueError):
        esvc.normalize_config({"scenario": "x"})


def test_normalize_config_truncates_description():
    """功能描述超过 300 字 -> 截断并加省略号（需求：不超过 300 字）。"""
    cfg = esvc.normalize_config({"system_prompt": "你是专家", "description": "字" * 400})
    assert len(cfg["description"]) == esvc.MAX_DESCRIPTION_LEN + 1
    assert cfg["description"].endswith("…")


def test_normalize_config_caps_skills_names():
    cfg = esvc.normalize_config(
        {"system_prompt": "你是专家", "skills_names": [f"标签{i}" for i in range(9)]}
    )
    assert len(cfg["skills_names"]) == esvc.MAX_SKILLS_NAMES


def test_normalize_config_defaults_for_new_fields():
    """老产物（没有新字段）不应报错，落空值由前端回退展示。"""
    cfg = esvc.normalize_config({"system_prompt": "你是专家"})
    assert cfg["description"] == ""
    assert cfg["skills_names"] == []
    assert cfg["tag_id"] == ""


def test_normalize_config_accepts_export_field_aliases():
    """兼容直接传库字段名（export_description / export_skills_names）的写法。"""
    cfg = esvc.normalize_config(
        {
            "system_prompt": "你是专家",
            "export_description": "库字段描述",
            "export_skills_names": ["宏观投研"],
        }
    )
    assert cfg["description"] == "库字段描述"
    assert cfg["skills_names"] == ["宏观投研"]


def test_normalize_config_coerces_types():
    cfg = esvc.normalize_config(
        {
            "system_prompt": "你是专家",
            "capabilities": "单条字符串",
            "workflow": ["步骤1", "", None, "步骤2"],
            "boundaries": [{"text": "边界1"}],
            "features_prompt": None,
        }
    )
    assert cfg["capabilities"] == ["单条字符串"]
    assert cfg["workflow"] == ["步骤1", "步骤2"]
    assert cfg["boundaries"] == ["边界1"]
    assert cfg["features_prompt"] == []


# ── 标识生成 ──────────────────────────────────────────────────────────


def test_slugify():
    assert esvc.slugify("Quant Strategy") == "quant-strategy"
    assert esvc.slugify("量化专家") == "expert"          # 中文无 ASCII 可留 -> 兜底
    assert esvc.slugify("") == "expert"
    assert esvc.slugify("a" * 100) != ""


def test_build_expert_ids_unique_and_no_random_suffix():
    id1, en1 = esvc.build_expert_ids("量化策略专家")
    id2, en2 = esvc.build_expert_ids("量化策略专家")
    assert id1 != id2                      # ID 每次唯一
    assert id1.startswith("expert_")
    # 英文名不含随机数字（需求 §1）：同名专家英文名相同，靠 agent_id 区分
    assert en1 == en2 == "expert"          # 纯中文名 slug 兜底
    assert not any(ch.isdigit() for ch in en1)


def test_build_expert_ids_ascii_name():
    agent_id, name_en = esvc.build_expert_ids("Quant Strategy")
    assert agent_id.startswith("expert_quant-strategy_")
    assert name_en == "quant-strategy"
    assert not any(ch.isdigit() for ch in name_en)


# ── 引导词 ────────────────────────────────────────────────────────────


def test_render_guide_falls_back_to_builtin(prompts_dir):
    """提示词目录里没有模板文件时，用内置兜底文案，且占位符被替换。"""
    guide = esvc.render_guide("量化专家", "我做了 8 年因子研究")
    assert "量化专家" in guide
    assert "我做了 8 年因子研究" in guide
    assert "{user_prompt}" not in guide


def test_render_guide_reads_file(prompts_dir):
    target = prompts_dir / "expert"
    target.mkdir(parents=True, exist_ok=True)
    (target / "create_agent_guide.md").write_text(
        "TEMPLATE name={name_cn} desc={user_prompt}", encoding="utf-8"
    )
    guide = esvc.render_guide("甲", "乙")
    assert guide == "TEMPLATE name=甲 desc=乙"


def test_render_tag_options_from_list_and_dict():
    text = esvc.render_tag_options(TAG_OPTIONS)
    assert "- macro_research: 宏观投研" in text
    assert "- factor_analysis: 因子分析" in text

    text2 = esvc.render_tag_options({"bond_research": "债券投研"})
    assert "- bond_research: 债券投研" in text2


def test_render_tag_options_empty_fallback():
    assert "other" in esvc.render_tag_options([])
    assert "other" in esvc.render_tag_options(None)


def test_render_guide_injects_tag_options(prompts_dir):
    target = prompts_dir / "expert"
    target.mkdir(parents=True, exist_ok=True)
    (target / "create_agent_guide.md").write_text(
        "name={name_cn}\ntags:\n{tag_options}", encoding="utf-8"
    )
    guide = esvc.render_guide("甲", "乙", TAG_OPTIONS)
    assert "- factor_analysis: 因子分析" in guide
    assert "{tag_options}" not in guide


# ── 分类标签白名单校验 ────────────────────────────────────────────────


def test_pick_tag_id_accepts_candidate():
    assert esvc.pick_tag_id("factor_analysis", TAG_OPTIONS) == "factor_analysis"


def test_pick_tag_id_rejects_unknown():
    """大模型编造的标签一律丢弃，避免污染筛选 chips。"""
    assert esvc.pick_tag_id("made_up_tag", TAG_OPTIONS) == ""
    assert esvc.pick_tag_id("", TAG_OPTIONS) == ""
    assert esvc.pick_tag_id("factor_analysis", []) == ""


# ── 参数校验 ──────────────────────────────────────────────────────────


def test_validate_create_params():
    esvc.validate_create_params("量化专家", "描述")  # 不抛异常

    with pytest.raises(ValueError):
        esvc.validate_create_params("", "描述")
    with pytest.raises(ValueError):
        esvc.validate_create_params("专家", "   ")
    with pytest.raises(ValueError):
        esvc.validate_create_params("x" * 51, "描述")
    with pytest.raises(ValueError):
        esvc.validate_create_params("专家", "x" * 4001)


# ── 版本目录 ──────────────────────────────────────────────────────────


def test_write_version_creates_files_and_active(ws):
    config = esvc.normalize_config(VALID_PAYLOAD)
    path = esvc.write_expert_version("testuser", "expert_x_12345678", 1, "量化专家", config)

    from pathlib import Path

    version_dir = Path(path)
    assert version_dir.is_dir()
    # 提示词按英文名命名（slug 兜底为 expert），不再有随机数字后缀
    assert (version_dir / "expert.md").is_file()
    assert (version_dir / "README.md").is_file()
    assert (version_dir / "config.yaml").is_file()
    assert (version_dir / "manifest.json").is_file()

    md = (version_dir / "expert.md").read_text(encoding="utf-8")
    assert md.startswith("---")                    # YAML frontmatter
    assert "name: expert" in md
    assert "# 量化专家" in md
    assert "## 核心能力" in md
    assert "## 工作流程" in md
    assert "## 注意事项" in md
    assert "不得违反系统的安全准则" in md          # 护栏文案
    assert "你是量化策略专家" in md

    readme = (version_dir / "README.md").read_text(encoding="utf-8")
    assert "# 量化专家" in readme
    assert "## 功能" in readme
    assert "## 使用示例" in readme
    assert "## 使用方式" in readme
    assert "v1" in readme

    assert esvc.read_active_version("testuser", "expert_x_12345678") == 1


def test_write_version_uses_name_en_for_prompt_file(ws):
    """LLM 生成 / 传入 name_en 时，提示词文件用该英文名命名。"""
    config = esvc.normalize_config(
        {
            **VALID_PAYLOAD,
            "name_en": "Quant Factor Advisor",
            "profession_zh": "资深量化研究员",
            "output_spec": ["先结论后论证", "数据注明来源与时间"],
        }
    )
    path = esvc.write_expert_version(
        "testuser", "e1", 1, "量化专家", config, name_en="quant-factor-advisor"
    )

    from pathlib import Path

    version_dir = Path(path)
    assert (version_dir / "quant-factor-advisor.md").is_file()
    md = (version_dir / "quant-factor-advisor.md").read_text(encoding="utf-8")
    assert "name: quant-factor-advisor" in md
    assert "zh: 资深量化研究员" in md
    assert "## 输出规范" in md
    assert "- 先结论后论证" in md


def test_write_version_records_new_metadata(ws):
    """版本目录的 config.yaml 记录功能描述 / 技能标签 / 分类标签。"""
    import yaml

    from pathlib import Path

    config = esvc.normalize_config(VALID_PAYLOAD)
    path = esvc.write_expert_version("testuser", "e1", 1, "量化专家", config)
    meta = yaml.safe_load(
        (Path(path) / "config.yaml").read_text(encoding="utf-8")
    )
    assert meta["description"] == config["description"]
    assert meta["skills_names"] == ["因子分析", "策略回测", "风险控制"]
    assert meta["tag_id"] == "factor_analysis"


def test_load_active_prompt_supports_legacy_expert_md(ws):
    """旧版本目录的 EXPERT.md 仍可被读取（向后兼容）。"""
    vdir = esvc.get_version_dir("testuser", "legacy", 1)
    vdir.mkdir(parents=True)
    (vdir / "EXPERT.md").write_text("你是旧版专家", encoding="utf-8")
    (vdir / "README.md").write_text("# 说明", encoding="utf-8")
    esvc.set_active_version("testuser", "legacy", 1)

    text, version, _ = esvc.load_active_prompt("testuser", "legacy")
    assert version == 1
    assert "你是旧版专家" in text


def test_write_version_does_not_overwrite_previous(ws):
    """修改专家时新建 v2 而不覆盖 v1（需求：防止旧版本运行中崩溃）。"""
    config = esvc.normalize_config(VALID_PAYLOAD)
    esvc.write_expert_version("testuser", "e1", 1, "甲", config)
    config2 = esvc.normalize_config({**VALID_PAYLOAD, "system_prompt": "你是第二代专家"})
    esvc.write_expert_version("testuser", "e1", 2, "甲", config2)

    assert esvc.list_version_dirs("testuser", "e1") == [1, 2]
    assert esvc.read_active_version("testuser", "e1") == 2

    v1_md = (esvc.get_version_dir("testuser", "e1", 1) / "expert.md").read_text("utf-8")
    assert "你是量化策略专家" in v1_md       # v1 内容仍在，未被覆盖


def test_load_active_prompt(ws):
    config = esvc.normalize_config(VALID_PAYLOAD)
    esvc.write_expert_version("testuser", "e1", 1, "甲", config)

    text, version, version_dir = esvc.load_active_prompt("testuser", "e1")
    assert version == 1
    assert "你是量化策略专家" in text
    assert version_dir.endswith("v1")


def test_load_active_prompt_missing_dir_degrades(ws):
    """目录不存在时返回空串（调用方降级为通用 Agent），不抛异常。"""
    text, version, version_dir = esvc.load_active_prompt("testuser", "not-exist")
    assert text == ""
    assert version == 0
    assert version_dir == ""


def test_read_active_version_falls_back_to_max(ws):
    config = esvc.normalize_config(VALID_PAYLOAD)
    esvc.write_expert_version("testuser", "e1", 1, "甲", config)
    esvc.write_expert_version("testuser", "e1", 2, "甲", config)

    (esvc.get_expert_dir("testuser", "e1") / "active").unlink()
    assert esvc.read_active_version("testuser", "e1") == 2


# ── 清理 ──────────────────────────────────────────────────────────────


def test_cleanup_keeps_newest_and_active(ws):
    config = esvc.normalize_config(VALID_PAYLOAD)
    for v in range(1, 6):
        esvc.write_expert_version("testuser", "e1", v, "甲", config, activate=False)
    # 让 active 指向被淘汰的旧版本，验证它不会被删
    esvc.set_active_version("testuser", "e1", 1)

    removed = esvc.cleanup_old_versions(
        "testuser", "e1", keep_versions=2, retain_days=0
    )
    remaining = esvc.list_version_dirs("testuser", "e1")

    assert 1 in remaining            # active 版本必须保留
    assert 5 in remaining and 4 in remaining
    assert sorted(removed) == [2, 3]


def test_cleanup_retain_days_protects_recent(ws):
    config = esvc.normalize_config(VALID_PAYLOAD)
    for v in range(1, 4):
        esvc.write_expert_version("testuser", "e1", v, "甲", config, activate=False)
    esvc.set_active_version("testuser", "e1", 3)

    removed = esvc.cleanup_old_versions(
        "testuser", "e1", keep_versions=1, retain_days=30
    )
    assert removed == []             # 全都还在保留期内
    assert esvc.list_version_dirs("testuser", "e1") == [1, 2, 3]


def test_remove_expert_dir(ws):
    config = esvc.normalize_config(VALID_PAYLOAD)
    esvc.write_expert_version("testuser", "e1", 1, "甲", config)
    assert esvc.get_expert_dir("testuser", "e1").is_dir()

    esvc.remove_expert_dir("testuser", "e1")
    assert not esvc.get_expert_dir("testuser", "e1").exists()

    esvc.remove_expert_dir("testuser", "not-exist")  # 幂等，不抛异常


# ── LLM 生成 ──────────────────────────────────────────────────────────


async def test_generate_expert_config_ok(prompts_dir):
    llm = FakeLLM(VALID_JSON)
    cfg = await esvc.generate_expert_config(llm, "量化专家", "我做了 8 年因子研究")
    assert cfg["system_prompt"].startswith("你是量化策略专家")
    assert llm.calls == 1


async def test_generate_expert_config_retries_on_bad_json(prompts_dir):
    llm = FakeLLM("这不是 JSON", VALID_JSON)
    cfg = await esvc.generate_expert_config(llm, "量化专家", "描述")
    assert cfg["capabilities"]
    assert llm.calls == 2            # 第一次失败，重试一次成功


async def test_generate_expert_config_raises_after_retries(prompts_dir):
    llm = FakeLLM("坏输出", "还是坏输出")
    with pytest.raises(ValueError):
        await esvc.generate_expert_config(llm, "量化专家", "描述")
    assert llm.calls == 2            # max_retries=1 -> 共 2 次


async def test_generate_expert_config_handles_exception(prompts_dir):
    llm = FakeLLM(RuntimeError("网络不通"), VALID_JSON)
    cfg = await esvc.generate_expert_config(llm, "量化专家", "描述")
    assert cfg["scenario"]
    assert llm.calls == 2


async def test_generate_expert_config_keeps_candidate_tag(prompts_dir):
    """LLM 从候选标签里选中 -> 保留。"""
    llm = FakeLLM(VALID_JSON)
    cfg = await esvc.generate_expert_config(
        llm, "量化专家", "描述", tag_options=TAG_OPTIONS
    )
    assert cfg["tag_id"] == "factor_analysis"


async def test_generate_expert_config_drops_unknown_tag(prompts_dir):
    """LLM 编造了候选之外的标签 -> 清空，不落脏数据。"""
    payload = {**VALID_PAYLOAD, "tag_id": "i_made_this_up"}
    llm = FakeLLM(json.dumps(payload, ensure_ascii=False))
    cfg = await esvc.generate_expert_config(
        llm, "量化专家", "描述", tag_options=TAG_OPTIONS
    )
    assert cfg["tag_id"] == ""


async def test_generate_expert_config_without_candidates_clears_tag(prompts_dir):
    """没配候选标签（或未传）时，tag_id 一律为空。"""
    llm = FakeLLM(VALID_JSON)
    cfg = await esvc.generate_expert_config(llm, "量化专家", "描述")
    assert cfg["tag_id"] == ""


async def test_generate_expert_config_carries_description_and_skills(prompts_dir):
    llm = FakeLLM(VALID_JSON)
    cfg = await esvc.generate_expert_config(llm, "量化专家", "描述")
    assert cfg["description"].startswith("面向量化投研场景")
    assert cfg["skills_names"] == ["因子分析", "策略回测", "风险控制"]
