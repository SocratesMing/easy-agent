"""技能库元数据测试：展示字段与框架加载技能用同一套 frontmatter 解析（YAML）。"""

from easy_agent.services import skill_library


def _skill(tmp_path, name, text):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def test_quoted_description_is_unescaped():
    """内置 pptx / xlsx 的 description 是带转义引号的 YAML 字符串，展示时不能留着反斜杠。"""
    text = '---\nname: pptx\ndescription: "mentions \\"deck,\\" or \\"slides\\""\n---\n'
    assert skill_library.parse_frontmatter(text)["description"] == 'mentions "deck," or "slides"'


def test_name_matches_what_the_framework_pins():
    """行尾注释不属于名字：pin 技能时框架按 YAML 解析出的 name 精确匹配。"""
    text = "---\nname: docx  # Word 文档\ndescription: 处理 Word\n---\n"
    assert skill_library.parse_frontmatter(text)["name"] == "docx"


def test_multiline_description():
    text = "---\nname: demo\ndescription: >\n  第一行\n  第二行\n---\n"
    assert skill_library.parse_frontmatter(text)["description"] == "第一行 第二行"


def test_invalid_yaml_falls_back_to_dir_name_and_body(tmp_path):
    """description 里没加引号的冒号不是合法 YAML，框架不会加载；展示按目录名和正文降级。"""
    text = "---\nname: demo\ndescription: Use when: 用户要写周报\n---\n# 周报助手\n帮你整理一周的工作。\n"
    assert skill_library.parse_frontmatter(text) == {}

    meta = skill_library.skill_metadata(_skill(tmp_path, "weekly", text))

    assert meta["name"] == "weekly"
    assert meta["description"] == "帮你整理一周的工作。"


def test_fallback_description_skips_the_frontmatter(tmp_path):
    """frontmatter 里没写 description 时取正文第一段，而不是 frontmatter 里的 name 行。"""
    text = "---\nname: demo\n---\n# 演示\n这是正文第一段。\n"
    meta = skill_library.skill_metadata(_skill(tmp_path, "demo", text))
    assert meta["name"] == "demo"
    assert meta["description"] == "这是正文第一段。"
