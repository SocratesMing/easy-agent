"""会话标题生成：输入框拼接的内部指令（连接器 / 技能）不能出现在标题里。"""

import easy_agent.api.chat as chat_api

SKILL_DIRS = [{"dir_name": "docx", "path": "/tmp/skills/docx", "enabled": True}]


def _patch_skills(monkeypatch, dirs=None, display_name="Word 文档"):
    """把用户技能目录固定下来，避免用例依赖本机实际安装的技能。"""
    monkeypatch.setattr(
        chat_api.skill_library,
        "list_user_skill_dirs",
        lambda username: list(SKILL_DIRS if dirs is None else dirs),
    )
    monkeypatch.setattr(
        chat_api.skill_library, "skill_metadata", lambda path: {"name": display_name}
    )


def test_title_keeps_user_text(monkeypatch):
    _patch_skills(monkeypatch)
    assert chat_api.generate_session_title("帮我做一个 word 文档", None, "u") == "帮我做一个 word 文档"


def test_title_strips_skill_prefix(monkeypatch):
    _patch_skills(monkeypatch)
    title = chat_api.generate_session_title("/docx 帮我做一个 word 文档", None, "u")
    assert title == "帮我做一个 word 文档"


def test_title_strips_several_skill_prefixes(monkeypatch):
    _patch_skills(monkeypatch, dirs=SKILL_DIRS + [{"dir_name": "pdf", "path": "/tmp/skills/pdf"}])
    assert chat_api.generate_session_title("/docx /pdf 对比一下", None, "u") == "对比一下"


def test_title_falls_back_to_skill_display_name(monkeypatch):
    """只挂了技能、没输入内容时用技能展示名兜底，原标题只剩一个斜杠指令。"""
    _patch_skills(monkeypatch)
    assert chat_api.generate_session_title("/docx", None, "u") == "Word 文档"


def test_title_keeps_path_like_prefix(monkeypatch):
    """目录名没命中用户技能时原样保留，避免误删路径。"""
    _patch_skills(monkeypatch)
    for message in ("/workspace 下的文件帮我看看", "/user-skills/这个路径在哪", "/usr/bin/env 是什么"):
        assert chat_api.generate_session_title(message, None, "u") == message


def test_title_strips_connector_directive(monkeypatch):
    _patch_skills(monkeypatch)
    title = chat_api.generate_session_title("使用连接器[高德地图] 帮我规划路线", None, "u")
    assert title == "帮我规划路线"


def test_title_strips_connector_then_skill(monkeypatch):
    """输入框把连接器指令拼在技能指令之前，两种前缀都要摘掉。"""
    _patch_skills(monkeypatch)
    message = "使用连接器[高德地图] /docx 帮我排个版"
    assert chat_api.generate_session_title(message, None, "u") == "帮我排个版"


def test_title_falls_back_to_connector_name(monkeypatch):
    _patch_skills(monkeypatch)
    assert chat_api.generate_session_title("使用连接器[高德地图]", None, "u") == "高德地图"


def test_title_truncates_long_text(monkeypatch):
    _patch_skills(monkeypatch)
    body = "很长的需求" * 10
    title = chat_api.generate_session_title(f"/docx {body}", None, "u")
    assert title == body[: chat_api.SESSION_TITLE_MAX_LEN] + "..."


def test_title_without_username_keeps_raw_message(monkeypatch):
    """拿不到用户（未登录/内部调用）时不去猜技能，但连接器指令仍按固定格式摘掉。"""
    _patch_skills(monkeypatch)
    assert chat_api.generate_session_title("/docx 你好", None, None) == "/docx 你好"
    assert chat_api.generate_session_title("使用连接器[高德地图] 你好", None, None) == "你好"


def test_title_uses_first_filename_without_message():
    assert chat_api.generate_session_title("", [{"filename": "报告.docx"}]) == "报告.docx"


def test_title_default_when_message_and_files_empty():
    assert chat_api.generate_session_title("", None) == "未命名会话"
