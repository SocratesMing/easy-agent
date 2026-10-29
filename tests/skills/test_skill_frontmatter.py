"""技能 frontmatter 启动期校验测试（FW-06）。"""

from easy_agent.agent.skills import parse_skill_frontmatter, skill_body, validate_skills


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



def test_frontmatter_ends_at_a_line_of_its_own():
    """和框架一样，结束标记是独占一行的 ---：值里出现 --- 不会把 frontmatter 截断。"""
    meta, error = parse_skill_frontmatter("---\nname: a---b\ndescription: x\n---\n# body\n")
    assert error is None
    assert meta["name"] == "a---b"
    # 结束 --- 后面没有换行：框架找不到 frontmatter、不会加载，这里同样报错
    assert parse_skill_frontmatter("---\nname: demo\ndescription: hi\n---")[1] is not None


def test_skill_body_strips_the_frontmatter():
    assert skill_body("---\nname: demo\n---\n# 标题\n正文\n") == "# 标题\n正文\n"
    assert skill_body("# 没有 frontmatter\n") == "# 没有 frontmatter\n"


def test_validate_skills_reports_errors(tmp_path):
    _write_skill(tmp_path, "good", "---\nname: good\ndescription: ok\n---\n")
    _write_skill(tmp_path, "no-desc", "---\nname: no-desc\n---\n")
    _write_skill(tmp_path, "broken", "# no frontmatter\n")

    results = {r["name"]: r["errors"] for r in validate_skills(str(tmp_path))}
    assert results["good"] == []
    assert results["no-desc"]
    assert results["broken"]
