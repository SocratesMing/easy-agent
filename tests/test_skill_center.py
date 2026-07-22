"""接口 /api/skill-center 的测试：公开技能、用户技能、添加与移除。"""
import os
import shutil
from pathlib import Path

SKILLS_ROOT = Path(os.environ["TEST_SKILLS_DIR"])


def _make_fake_skill(name: str) -> Path:
    d = SKILLS_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# Fake Skill\n", encoding="utf-8")
    return d


def test_public_skills(client):
    resp = client.get("/api/skill-center/public-skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_user_skills(client):
    resp = client.get("/api/skill-center/user-skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert isinstance(data["skills"], list)


def test_add_and_remove_skill(client):
    name = "unit_test_skill"
    _make_fake_skill(name)
    try:
        add = client.post("/api/skill-center/add-skill", json={"dir_name": name})
        assert add.status_code == 200

        # 添加后出现在用户技能列表中
        user = client.get("/api/skill-center/user-skills").json()
        assert name in [s.get("dir_name") for s in user["skills"]]

        rem = client.post("/api/skill-center/remove-skill", json={"dir_name": name})
        assert rem.status_code == 200

        user2 = client.get("/api/skill-center/user-skills").json()
        assert name not in [s.get("dir_name") for s in user2["skills"]]
    finally:
        d = SKILLS_ROOT / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
