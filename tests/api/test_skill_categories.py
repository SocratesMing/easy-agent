"""接口 /agent/skill-center/categories 等：技能分类的增删改、排序与归属。

覆盖需求：分类分组展示所需的数据、类别自动生成（技能创建时按内容归类）、
自定义类别、类别编辑（重命名/删除/排序/改归属）、持久化。
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from easy_agent.services import skill_categories, skill_library

SKILLS_ROOT = Path(os.environ["TEST_SKILLS_DIR"])
BASE = "/agent/skill-center"

@pytest.fixture(autouse=True)
def _clean_state(client):
    """用例前后清空用户技能目录与类别文件，避免相互污染。"""
    store = skill_categories.store_path("testuser")
    roots = [skill_library.user_skills_dir("testuser"), skill_library.user_disabled_dir("testuser")]
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)
    store.unlink(missing_ok=True)
    yield
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)
    store.unlink(missing_ok=True)


def _make_skill(name: str, content: str = "# Fake Skill\n") -> Path:
    d = SKILLS_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


def _category_names(client) -> list[str]:
    return [item["name"] for item in client.get(f"{BASE}/categories").json()["categories"]]


def test_categories_initialized_with_builtin_presets(client):
    data = client.get(f"{BASE}/categories").json()

    assert data["categories"], "首次访问应写入内置预置类别"
    assert data["default_category"] == {"id": "default", "name": "通用技能"}
    ids = [item["id"] for item in data["categories"]]
    assert "doc" in ids and "code" in ids
    # 预置类别带顺序，供前端分组排序
    assert all(isinstance(item["order"], int) for item in data["categories"])
    assert skill_categories.store_path("testuser").exists()


def test_create_rename_delete_category(client):
    created = client.post(f"{BASE}/create-category", json={"name": "投资理财"})
    assert created.status_code == 200
    category_id = created.json()["category"]["id"]
    assert "投资理财" in _category_names(client)

    # 重名 / 空名都不允许
    assert client.post(f"{BASE}/create-category", json={"name": "投资理财"}).status_code == 400
    assert client.post(f"{BASE}/create-category", json={"name": "  "}).status_code == 400

    renamed = client.post(
        f"{BASE}/rename-category", json={"category_id": category_id, "name": "理财工具"}
    )
    assert renamed.status_code == 200
    assert "理财工具" in _category_names(client)

    assert client.post(
        f"{BASE}/rename-category", json={"category_id": "not-exist", "name": "x"}
    ).status_code == 404

    deleted = client.post(f"{BASE}/delete-category", json={"category_id": category_id})
    assert deleted.status_code == 200
    assert "理财工具" not in _category_names(client)
    assert client.post(f"{BASE}/delete-category", json={"category_id": category_id}).status_code == 404


def test_reorder_categories(client):
    before = client.get(f"{BASE}/categories").json()["categories"]
    last_id = before[-1]["id"]

    data = client.post(f"{BASE}/reorder-categories", json={"category_ids": [last_id]}).json()

    assert data["categories"][0]["id"] == last_id
    assert len(data["categories"]) == len(before)
    # 未列出的类别仍在，且顺序保持稳定（order 重新编号）
    assert [item["id"] for item in data["categories"]] == [
        last_id,
        *[item["id"] for item in before if item["id"] != last_id],
    ]
    assert [item["order"] for item in data["categories"]] == [
        (index + 1) * 10 for index in range(len(before))
    ]


def test_skill_created_by_content_is_auto_categorized(client):
    """需求 2：新增技能时按内容自动识别类别，无需手动指定。"""
    cases = {
        "auto_cat_doc": (
            "---\nname: PDF 文档转换\ndescription: 把 PDF 转成 Word 文档\n---\n",
            "doc",
        ),
        "auto_cat_data": (
            "---\nname: 销售数据分析\ndescription: 数据清洗、指标统计与报表可视化\n---\n",
            "data",
        ),
        "auto_cat_design": (
            "---\nname: 品牌海报设计\ndescription: 生成海报与配色方案\n---\n",
            "design",
        ),
    }
    for name in cases:
        _make_skill(name, cases[name][0])

    for name, (_, expected) in cases.items():
        added = client.post(f"{BASE}/add-skill", json={"dir_name": name})
        assert added.status_code == 200, name
        assert added.json()["category_id"] == expected, name

    rows = {item["dir_name"]: item for item in client.get(f"{BASE}/user-skills").json()["skills"]}
    for name, (_, expected) in cases.items():
        assert rows[name]["category_id"] == expected
        assert rows[name]["category_name"]


def test_long_body_code_keywords_do_not_override_description(client):
    """正文里的技术词汇不能压过技能名/描述。

    真实案例：docx 技能正文 20KB 且带脚本，满是 python / git / npm，等权参与时会被
    误判成「代码开发」；名称与描述应占更高权重。
    """
    name = "weighted_auto_cat_demo"
    noisy_body = "\n".join(
        ["---", "name: Word 文档处理", "description: 读写与转换 Word 文档", "---", ""]
        + ["python javascript java git npm 脚本示例"] * 50
    )
    _make_skill(name, noisy_body)

    added = client.post(f"{BASE}/add-skill", json={"dir_name": name})

    assert added.json()["category_id"] == "doc"


def test_auto_categorize_matches_custom_category_name(client):
    """自定义类别的名称也参与自动识别：技能内容提到它时自动归入该类。"""
    name = "auto_cat_finance"
    _make_skill(
        name,
        "---\nname: 自选股助手\ndescription: 投资理财场景下的自选股与基金净值查询\n---\n",
    )
    finance_id = client.post(
        f"{BASE}/create-category", json={"name": "投资理财"}
    ).json()["category"]["id"]

    added = client.post(f"{BASE}/add-skill", json={"dir_name": name})

    assert added.json()["category_id"] == finance_id
    rows = client.get(f"{BASE}/user-skills").json()["skills"]
    assert next(r for r in rows if r["dir_name"] == name)["category_id"] == finance_id


def test_assign_and_restore_auto_category(client):
    name = "assign_demo"
    _make_skill(name, "---\nname: PDF 文档转换\ndescription: PDF 转 Word\n---\n")
    client.post(f"{BASE}/add-skill", json={"dir_name": name})

    rows = client.get(f"{BASE}/user-skills").json()["skills"]
    assert next(r for r in rows if r["dir_name"] == name)["category_id"] == "doc"

    custom = client.post(f"{BASE}/create-category", json={"name": "我的分类"}).json()["category"]["id"]
    assigned = client.post(
        f"{BASE}/assign-category", json={"dir_name": name, "category_id": custom}
    ).json()
    assert assigned["category_id"] == custom
    assert assigned["category_name"] == "我的分类"

    rows = client.get(f"{BASE}/user-skills").json()["skills"]
    assert next(r for r in rows if r["dir_name"] == name)["category_id"] == custom

    # 空字符串 = 恢复自动归类，返回重新推断的结果
    restored = client.post(
        f"{BASE}/assign-category", json={"dir_name": name, "category_id": ""}
    ).json()
    assert restored["category_id"] == "doc"


def test_assign_rejects_unknown_skill_and_category(client):
    assert client.post(
        f"{BASE}/assign-category", json={"dir_name": "not-exist", "category_id": "doc"}
    ).status_code == 404

    name = "assign_unknown_demo"
    _make_skill(name)
    client.post(f"{BASE}/add-skill", json={"dir_name": name})
    assert client.post(
        f"{BASE}/assign-category", json={"dir_name": name, "category_id": "not-exist"}
    ).status_code == 404


def test_delete_category_resets_skill_assignment(client):
    name = "delete_reset_demo"
    _make_skill(name)
    client.post(f"{BASE}/add-skill", json={"dir_name": name})

    custom = client.post(f"{BASE}/create-category", json={"name": "临时分类"}).json()["category"]["id"]
    client.post(f"{BASE}/assign-category", json={"dir_name": name, "category_id": custom})

    deleted = client.post(f"{BASE}/delete-category", json={"category_id": custom}).json()
    assert deleted["unassigned"] == [name]

    store = skill_categories.load_store("testuser")
    assert name not in store["assignments"]


def test_categories_are_persisted_per_user(client):
    """需求 5：类别与归属落盘保存，重读仍一致。"""
    name = "persist_demo"
    _make_skill(name)
    client.post(f"{BASE}/add-skill", json={"dir_name": name})
    custom = client.post(f"{BASE}/create-category", json={"name": "持久化分类"}).json()["category"]["id"]
    client.post(f"{BASE}/assign-category", json={"dir_name": name, "category_id": custom})

    path = skill_categories.store_path("testuser")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["assignments"][name] == custom
    assert any(item["name"] == "持久化分类" for item in raw["categories"])

    # 直接从文件重读（模拟进程重启）：类别与归属不变
    store = skill_categories.load_store("testuser")
    assert store["assignments"][name] == custom
    assert any(item["name"] == "持久化分类" for item in store["categories"])


def test_broken_store_falls_back_to_builtin(client):
    """类别文件损坏时不应让技能中心整体不可用。"""
    path = skill_categories.store_path("testuser")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not-json", encoding="utf-8")

    data = client.get(f"{BASE}/categories").json()
    assert [item["id"] for item in data["categories"]][:1] == ["doc"]


# ── 分类在创建时定稿，之后不再变动 ─────────────────────────────────────

def test_category_is_finalized_at_creation(client):
    """创建即定稿：即使推断为「通用技能」也写入归属记录（不再实时推断）。"""
    name = "finalize_default_demo"
    _make_skill(name, "---\nname: zzz\ndescription: qqq\n---\n")

    added = client.post(f"{BASE}/add-skill", json={"dir_name": name})

    assert added.json()["category_id"] == "default"
    assert skill_categories.load_store("testuser")["assignments"][name] == "default"


def test_category_does_not_change_after_creation(client):
    """分类定稿后不再随内容变动：技能内容换领域后仍用创建时的分类。"""
    name = "frozen_category_demo"
    _make_skill(name, "---\nname: 销售数据分析\ndescription: 数据清洗与报表\n---\n")
    client.post(f"{BASE}/add-skill", json={"dir_name": name})
    rows = client.get(f"{BASE}/user-skills").json()["skills"]
    assert next(r for r in rows if r["dir_name"] == name)["category_id"] == "data"

    # 把已入库的技能改成完全不同的领域：分类仍是创建时定下的「数据分析」
    (skill_library.user_skills_dir("testuser") / name / "SKILL.md").write_text(
        "---\nname: 品牌海报设计\ndescription: 生成海报与配色方案\n---\n",
        encoding="utf-8",
    )

    rows = client.get(f"{BASE}/user-skills").json()["skills"]
    assert next(r for r in rows if r["dir_name"] == name)["category_id"] == "data"


def test_legacy_skill_assignment_backfilled_on_listing(client):
    """早于该规则的历史技能（无归属记录）在首次列出时按内容补录一次。"""
    name = "legacy_backfill_demo"
    legacy = skill_library.user_skills_dir("testuser") / name
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "SKILL.md").write_text(
        "---\nname: PDF 文档转换\ndescription: PDF 转 Word\n---\n", encoding="utf-8"
    )
    assert not skill_categories.load_store("testuser")["assignments"].get(name)

    rows = client.get(f"{BASE}/user-skills").json()["skills"]

    assert next(r for r in rows if r["dir_name"] == name)["category_id"] == "doc"
    assert skill_categories.load_store("testuser")["assignments"][name] == "doc"
