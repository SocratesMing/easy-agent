"""技能 frontmatter 启动期校验测试（FW-06）。"""

from easy_agent.skills import parse_skill_frontmatter, validate_skills


def _write_skill(root, name, text):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")


def test_parse_skill_frontmatter_ok():
    meta, error = parse_skill_frontmatter("---\nname: demo\ndescription: hi\n---\n# body\n")
    assert error is None
    assert meta["name"] == "demo"


def test_parse_skill_frontmatter_rejects_missing_or_broken():
    assert parse_skill_frontmatter("# no frontmatter")[1] is not None
    assert parse_skill_frontmatter("---\nname: x\n")[1] is not None
    assert parse_skill_frontmatter("---\n: bad yaml\n---\n")[1] is not None


def test_validate_skills_reports_errors(tmp_path):
    _write_skill(tmp_path, "good", "---\nname: good\ndescription: ok\n---\n")
    _write_skill(tmp_path, "no-desc", "---\nname: no-desc\n---\n")
    _write_skill(tmp_path, "broken", "# no frontmatter\n")

    results = {r["name"]: r["errors"] for r in validate_skills(str(tmp_path))}
    assert results["good"] == []
    assert results["no-desc"]
    assert results["broken"]
