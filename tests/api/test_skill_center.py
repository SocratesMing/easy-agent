"""接口 /agent/skill-center 的测试：公开技能、用户技能、添加与移除、启用/停用、详情。"""
import os
import shutil
from pathlib import Path

import pytest

from easy_agent.services import skill_library

SKILLS_ROOT = Path(os.environ["TEST_SKILLS_DIR"])

SKILL_MD = """---
name: 演示技能
description: 用于单测的技能描述
icon: 🧪
version: 1.2.3
---

# 演示技能

正文内容 body-marker
"""


def _make_fake_skill(name: str) -> Path:
    d = SKILLS_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# Fake Skill\n", encoding="utf-8")
    return d


def _make_rich_skill(root: Path, name: str) -> Path:
    """带 frontmatter 与附加文件的技能，用于统计/详情断言。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (d / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _clean_user_skill_dirs(client):
    """用例前后清空用户技能（启用/停用两个目录），避免相互污染。

    依赖 client 夹具是为了确保 Config.get_user_workspace_dir 已重定向到临时目录。
    """
    roots = [
        skill_library.user_skills_dir("testuser"),
        skill_library.user_disabled_dir("testuser"),
    ]
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)
    yield
    for root in roots:
        shutil.rmtree(root, ignore_errors=True)


def test_public_skills(client):
    resp = client.get("/agent/skill-center/public-skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_user_skills(client):
    resp = client.get("/agent/skill-center/user-skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_add_and_remove_skill(client):
    name = "unit_test_skill"
    _make_fake_skill(name)
    try:
        add = client.post("/agent/skill-center/add-skill", json={"dir_name": name})
        assert add.status_code == 200

        # 添加后出现在用户技能列表中
        user = client.get("/agent/skill-center/user-skills").json()
        assert name in [s.get("dir_name") for s in user["skills"]]

        rem = client.post("/agent/skill-center/remove-skill", json={"dir_name": name})
        assert rem.status_code == 200

        user2 = client.get("/agent/skill-center/user-skills").json()
        assert name not in [s.get("dir_name") for s in user2["skills"]]
    finally:
        d = SKILLS_ROOT / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


# ── 统计信息 ──────────────────────────────────────────────────────────


def test_user_skill_list_includes_stats_and_version(client):
    name = "stats_demo"
    _make_rich_skill(skill_library.user_skills_dir("testuser"), name)

    data = client.get("/agent/skill-center/user-skills").json()
    row = next(s for s in data["skills"] if s["dir_name"] == name)

    assert row["enabled"] is True
    assert row["version"] == "1.2.3"
    assert row["description"] == "用于单测的技能描述"
    assert row["stats"]["file_count"] == 2          # SKILL.md + helper.py
    assert row["stats"]["size_bytes"] > 0
    assert row["stats"]["updated_at"] is not None
    assert data["enabled_count"] == 1


def test_description_fallback_skips_noise_lines(client):
    """没有 frontmatter description 时，跳过纯符号行（如只有 ">" 的引用符号）。"""
    name = "noisy_desc_demo"
    d = skill_library.user_skills_dir("testuser") / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# 标题\n\n>\n\n真正的技能描述\n", encoding="utf-8")

    row = next(
        s for s in client.get("/agent/skill-center/user-skills").json()["skills"]
        if s["dir_name"] == name
    )
    assert row["description"] == "真正的技能描述"


def test_public_skill_list_includes_stats(client):
    name = "public_stats_demo"
    _make_rich_skill(SKILLS_ROOT, name)
    try:
        data = client.get("/agent/skill-center/public-skills").json()
        row = next(s for s in data["skills"] if s["dir_name"] == name)
        assert row["stats"]["file_count"] == 2
        assert row["added"] is False
        assert row["enabled"] is False
        assert row["version"] == "1.2.3"
    finally:
        shutil.rmtree(SKILLS_ROOT / name, ignore_errors=True)


# ── 启用 / 停用 ───────────────────────────────────────────────────────


def test_disable_and_enable_moves_directory(client):
    name = "toggle_demo"
    _make_rich_skill(skill_library.user_skills_dir("testuser"), name)

    resp = client.post("/agent/skill-center/disable-skill", json={"dir_name": name})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # 目录确实被移出 Agent 加载目录
    assert not (skill_library.user_skills_dir("testuser") / name).exists()
    assert (skill_library.user_disabled_dir("testuser") / name).exists()

    data = client.get("/agent/skill-center/user-skills").json()
    row = next(s for s in data["skills"] if s["dir_name"] == name)
    assert row["enabled"] is False
    assert data["enabled_count"] == 0

    resp = client.post("/agent/skill-center/enable-skill", json={"dir_name": name})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert (skill_library.user_skills_dir("testuser") / name).exists()
    assert not (skill_library.user_disabled_dir("testuser") / name).exists()


def test_toggle_is_idempotent_and_404_for_unknown(client):
    name = "toggle_idempotent"
    _make_rich_skill(skill_library.user_skills_dir("testuser"), name)

    assert client.post("/agent/skill-center/disable-skill", json={"dir_name": name}).status_code == 200
    # 重复停用：已经是停用态，返回成功而不是 4xx/5xx
    again = client.post("/agent/skill-center/disable-skill", json={"dir_name": name})
    assert again.status_code == 200
    assert again.json()["enabled"] is False

    assert client.post("/agent/skill-center/enable-skill", json={"dir_name": "no-such-skill"}).status_code == 404
    assert client.post("/agent/skill-center/disable-skill", json={"dir_name": "no-such-skill"}).status_code == 404


def test_public_skill_installed_flag_tracks_disabled_state(client):
    name = "public_toggle_demo"
    _make_rich_skill(SKILLS_ROOT, name)
    try:
        assert client.post("/agent/skill-center/add-skill", json={"dir_name": name}).status_code == 200
        row = next(
            s for s in client.get("/agent/skill-center/public-skills").json()["skills"]
            if s["dir_name"] == name
        )
        assert row["added"] is True and row["enabled"] is True

        client.post("/agent/skill-center/disable-skill", json={"dir_name": name})
        row = next(
            s for s in client.get("/agent/skill-center/public-skills").json()["skills"]
            if s["dir_name"] == name
        )
        assert row["added"] is True and row["enabled"] is False
    finally:
        shutil.rmtree(SKILLS_ROOT / name, ignore_errors=True)


def test_remove_skill_also_removes_disabled_copy(client):
    name = "remove_disabled_demo"
    _make_rich_skill(skill_library.user_skills_dir("testuser"), name)
    client.post("/agent/skill-center/disable-skill", json={"dir_name": name})

    assert client.post("/agent/skill-center/remove-skill", json={"dir_name": name}).status_code == 200
    assert not (skill_library.user_disabled_dir("testuser") / name).exists()
    assert client.get("/agent/skill-center/user-skills").json()["skills"] == []


# ── 详情 ──────────────────────────────────────────────────────────────


def test_skill_detail_public_and_user(client):
    name = "detail_demo"
    _make_rich_skill(SKILLS_ROOT, name)
    try:
        resp = client.get("/agent/skill-center/skill-detail", params={"dir_name": name, "scope": "public"})
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["scope"] == "public"
        assert detail["doc_file"] == "SKILL.md"
        assert "body-marker" in detail["content"]
        assert detail["version"] == "1.2.3"
        assert detail["stats"]["file_count"] == 2
        assert detail["added"] is False and detail["enabled"] is False

        # 装到用户目录后再看：user 作用域能读到正文，且标记为已启用
        client.post("/agent/skill-center/add-skill", json={"dir_name": name})
        detail = client.get(
            "/agent/skill-center/skill-detail", params={"dir_name": name, "scope": "user"}
        ).json()
        assert detail["enabled"] is True
        assert "body-marker" in detail["content"]
        assert detail["path"]

        # 停用后 scope=user 依然能读到（含已停用），但 enabled=False
        client.post("/agent/skill-center/disable-skill", json={"dir_name": name})
        detail = client.get(
            "/agent/skill-center/skill-detail", params={"dir_name": name, "scope": "user"}
        ).json()
        assert detail["enabled"] is False
    finally:
        shutil.rmtree(SKILLS_ROOT / name, ignore_errors=True)


def test_skill_detail_rejects_bad_input(client):
    assert client.get(
        "/agent/skill-center/skill-detail", params={"dir_name": "x", "scope": "other"}
    ).status_code == 400
    assert client.get(
        "/agent/skill-center/skill-detail", params={"dir_name": "not-exist", "scope": "public"}
    ).status_code == 404
    assert client.get(
        "/agent/skill-center/skill-detail", params={"dir_name": "not-exist", "scope": "user"}
    ).status_code == 404
