"""上传文件解析落盘 + 清单记忆（file_context）测试。

覆盖范围：
- 配置解析：默认值、缺失该段、``${VAR:-default}`` 插值、``enabled=false`` 开关；
- 上传即落盘：解析正文写入会话工作区 ``uploads/{文件名}.txt``（含元信息头）；
- 上传即记清单：``uploads/index.md`` 记录文件名/大小/状态/正文相对路径，
  超上限时只丢最旧条目；
- 解析失败的两种形态都被如实标记（解析器返回空、解析器返回方括号占位说明）；
- 超过 ``parse_max_file_size_mb`` 的文件不解析但仍进清单；
- 本轮预览：从落盘正文取开头片段；``preview_max_chars=0`` 时不注入；
- ``enabled=false`` 时整体跳过（不落盘、不建清单），回退旧行为。

背景见 ``easy_agent/services/session_uploads.py`` 顶部说明：改造前解析全文只塞进
"本轮带文件"的提示词，第二轮起模型看不到内容；改造后正文落盘、清单进记忆，
模型按需用 grep/read_file 检索。
"""

from pathlib import Path

import pytest

from easy_agent.bootstrap.config import Config, FileContextConfig
from easy_agent.services import session_uploads as su

_MINIMAL_CONFIG = """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: sk-test
"""


def _write_config(tmp_path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(_MINIMAL_CONFIG + extra, encoding="utf-8")
    return path


# ── 配置解析 ──────────────────────────────────────────────────────────


def test_file_context_defaults_when_section_missing(tmp_path):
    """老配置文件没有 file_context 段时应回落默认值，不影响启动。"""
    config = Config.from_yaml(_write_config(tmp_path))

    assert config.file_context.enabled is True
    assert config.file_context.parse_max_file_size_mb == 20
    assert config.file_context.max_stored_chars == 200000
    assert config.file_context.manifest_max_chars == 2000
    assert config.file_context.preview_max_chars == 2000


def test_file_context_parsed_from_yaml(tmp_path, monkeypatch):
    """支持 ${VAR:-default} 占位符，且各字段可独立配置。"""
    monkeypatch.setenv("FILE_PARSE_MAX_MB", "5")
    config = Config.from_yaml(
        _write_config(
            tmp_path,
            extra="""
file_context:
  enabled: ${FILE_CONTEXT_ENABLED:-true}
  parse_max_file_size_mb: ${FILE_PARSE_MAX_MB:-20}
  max_stored_chars: ${FILE_MAX_STORED_CHARS:-1000}
  manifest_max_chars: ${FILE_MANIFEST_MAX_CHARS:-800}
  preview_max_chars: ${FILE_PREVIEW_MAX_CHARS:-0}
""",
        )
    )

    assert config.file_context.parse_max_file_size_mb == 5
    assert config.file_context.max_stored_chars == 1000
    assert config.file_context.manifest_max_chars == 800
    assert config.file_context.preview_max_chars == 0


def test_invalid_file_context_section_falls_back(tmp_path):
    """该段写成非字典（例如误写成字符串）时不抛异常，按默认值处理。"""
    config = Config.from_yaml(_write_config(tmp_path, extra="file_context: 不是字典\n"))

    assert config.file_context.enabled is True
    assert config.file_context.parse_max_file_size_mb == 20


# ── 测试内工具 ────────────────────────────────────────────────────────


def _create_session(client) -> str:
    resp = client.post("/agent/sessions", json={"title": "文件上下文测试"})
    assert resp.status_code == 200
    return resp.json()["session_id"]


def _ws_dir(db, session_id: str) -> Path:
    """会话工作区目录（与 agent.py 口径一致：session/{workspace_name}）。"""
    session = db.get_session(session_id)
    name = (getattr(session, "workspace_name", "") or "") or session_id
    return Path(__import__("os").environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser" / "session" / name


def _patch_fc(monkeypatch, **kwargs):
    """改全局配置里的 file_context（模拟 config.yaml 被修改）。"""
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")
    base = FileContextConfig()
    merged = FileContextConfig(**{**base.model_dump(), **kwargs})
    monkeypatch.setattr(am._agent_config["config"], "file_context", merged, raising=False)
    return merged


def _upload(client, sid: str, name: str, data: bytes, mime: str = "text/plain"):
    return client.post(
        f"/agent/sessions/{sid}/upload", files={"file": (name, data, mime)}
    )


# ── 上传落盘 ──────────────────────────────────────────────────────────


def test_upload_stores_parsed_text_and_manifest(client, db):
    """上传 txt：正文落盘 uploads/note.txt，清单 uploads/index.md 记录该文件。"""
    sid = _create_session(client)
    body = "本季度营收为 123 亿元，同比增长 8%。"

    resp = _upload(client, sid, "note.txt", body.encode("utf-8"))
    assert resp.status_code == 200
    data = resp.json()

    assert data["parsed"] is True
    assert data["parsed_path"] == "uploads/note.txt"
    assert data["parse_status"].startswith("已解析")

    ws = _ws_dir(db, sid)
    stored = ws / "uploads" / "note.txt"
    assert stored.exists()
    text = stored.read_text(encoding="utf-8")
    assert body in text
    # 元信息头（供人工排查，模型读到的也是同一份文件）
    assert "<!--" in text and "note.txt" in text

    manifest = (ws / "uploads" / "index.md").read_text(encoding="utf-8")
    assert "note.txt" in manifest
    assert "已解析" in manifest
    assert "uploads/note.txt" in manifest
    assert "grep" in manifest  # 清单里带检索说明，注入记忆后即模型的行动指引


def test_manifest_keeps_newest_when_over_limit(client, db, monkeypatch):
    """清单超上限时只丢最旧条目，最新上传始终保留。"""
    # 预算按"上限 - 头部说明"计算，这里收紧到只够放 1~2 条
    _patch_fc(monkeypatch, manifest_max_chars=len(su.MANIFEST_HEADER) + 100)
    sid = _create_session(client)

    names = ["第一个文件.txt", "第二个文件.txt", "第三个文件.txt", "第四个文件.txt"]
    for name in names:
        _upload(client, sid, name, ("内容-" + name).encode("utf-8"))

    manifest = (_ws_dir(db, sid) / "uploads" / "index.md").read_text(encoding="utf-8")
    lines = [ln for ln in manifest.splitlines() if ln.startswith("- ")]

    assert names[-1] in manifest
    # 保留了最新条目、丢掉了更早的条目
    assert len(lines) < len(names)
    assert names[0] not in manifest
    assert su.MANIFEST_HEADER in manifest  # 头部检索说明始终保留


def test_parse_failure_empty_result_is_marked(client, db, monkeypatch):
    """解析器拿不到文本时标记为解析失败，不误报"已解析"。"""
    sid = _create_session(client)
    monkeypatch.setattr(su, "parse_file_content", lambda _p: "")

    resp = _upload(client, sid, "empty.txt", b"whatever")
    assert resp.status_code == 200
    assert resp.json()["parsed"] is True
    assert "解析失败" in resp.json()["parse_status"]

    manifest = (_ws_dir(db, sid) / "uploads" / "index.md").read_text(encoding="utf-8")
    assert "解析失败" in manifest


def test_parse_placeholder_result_is_marked(client, db, monkeypatch):
    """解析器返回方括号占位说明（如"未安装PDF解析库"）时同样标记为解析失败。"""
    sid = _create_session(client)
    monkeypatch.setattr(
        su,
        "parse_file_content",
        lambda _p: "[PDF文件: x.pdf，未安装PDF解析库，请安装: uv pip install pymupdf]",
    )

    resp = _upload(client, sid, "x.pdf", b"%PDF-1.4 broken", "application/pdf")
    assert resp.status_code == 200
    assert "解析失败" in resp.json()["parse_status"]

    manifest = (_ws_dir(db, sid) / "uploads" / "index.md").read_text(encoding="utf-8")
    assert "parse_placeholder" not in manifest  # 不泄露测试名，仅确认走的是失败分支
    assert "解析失败" in manifest


def test_large_file_skips_parse_but_listed(client, db, monkeypatch):
    """超过 parse_max_file_size_mb 的文件不解析，但仍进入清单（模型知道它存在）。"""
    _patch_fc(monkeypatch, parse_max_file_size_mb=1)
    sid = _create_session(client)

    resp = _upload(client, sid, "big.txt", b"a" * (2 * 1024 * 1024))
    assert resp.status_code == 200
    assert "未解析" in resp.json()["parse_status"]

    stored = _ws_dir(db, sid) / "uploads" / "big.txt"
    assert stored.exists()
    text = stored.read_text(encoding="utf-8")
    assert "超过可解析上限" in text

    manifest = (_ws_dir(db, sid) / "uploads" / "index.md").read_text(encoding="utf-8")
    assert "big.txt" in manifest and "未解析" in manifest


def test_same_name_reupload_marks_updated(client, db):
    """同名文件重复上传：正文覆盖，清单标注"本次已更新"。"""
    sid = _create_session(client)
    _upload(client, sid, "report.txt", "第一版内容".encode("utf-8"))
    _upload(client, sid, "report.txt", "第二版内容".encode("utf-8"))

    ws = _ws_dir(db, sid)
    assert "第二版内容" in (ws / "uploads" / "report.txt").read_text(encoding="utf-8")
    manifest = (ws / "uploads" / "index.md").read_text(encoding="utf-8")
    assert "本次已更新" in manifest


# ── 本轮预览 ──────────────────────────────────────────────────────────


def test_turn_preview_reads_stored_text(client, db):
    """预览取落盘正文的开头片段（不含元信息头），并指向 uploads/ 目录。"""
    sid = _create_session(client)
    _upload(client, sid, "preview.txt", "关键结论：库存周转率 3.2 次。".encode("utf-8"))

    preview = su.build_turn_preview(
        sid, "testuser", [{"filename": "preview.txt"}], db
    )
    assert "关键结论：库存周转率 3.2 次。" in preview
    assert "uploads/" in preview
    assert "<!--" not in preview  # 元信息头不进模型输入


def test_turn_preview_respects_zero_limit(client, db, monkeypatch):
    """preview_max_chars=0 时不注入预览（完全依赖按需检索）。"""
    sid = _create_session(client)
    _upload(client, sid, "p.txt", "内容".encode("utf-8"))
    _patch_fc(monkeypatch, preview_max_chars=0)

    assert su.build_turn_preview(sid, "testuser", [{"filename": "p.txt"}], db) == ""


def test_turn_preview_truncates_to_limit(client, db, monkeypatch):
    """预览总量受 preview_max_chars 限制。"""
    sid = _create_session(client)
    _upload(client, sid, "long.txt", ("段落内容。" * 500).encode("utf-8"))
    _patch_fc(monkeypatch, preview_max_chars=120)

    preview = su.build_turn_preview(
        sid, "testuser", [{"filename": "long.txt"}], db
    )
    assert preview
    assert len(preview) < 400  # 说明文字 + 不超过 120 字符的正文片段


# ── 开关 ──────────────────────────────────────────────────────────────


def test_disabled_skips_store_and_manifest(client, db, monkeypatch):
    """enabled=false：不落盘、不建清单（回退旧的全量注入行为）。"""
    _patch_fc(monkeypatch, enabled=False)
    sid = _create_session(client)

    resp = _upload(client, sid, "off.txt", "内容".encode("utf-8"))
    assert resp.status_code == 200
    assert resp.json()["parsed"] is False
    assert resp.json()["parsed_path"] is None

    ws = _ws_dir(db, sid)
    assert not (ws / "uploads" / "index.md").exists()
    assert su.build_turn_preview(sid, "testuser", [{"filename": "off.txt"}], db) == ""


def test_manifest_path_matches_agent_expectation(client, db):
    """清单路径契约：会话工作区下的 uploads/index.md（agent.py 按此拼虚拟路径）。"""
    sid = _create_session(client)
    path = su.get_manifest_path(sid, "testuser", db)

    assert path is not None
    assert path.parent.name == su.UPLOADS_DIR_NAME
    assert path.name == su.MANIFEST_FILENAME
    assert path == _ws_dir(db, sid) / "uploads" / "index.md"


# ── 按需重解析（首次解析失败后提问时自动重试） ────────────────────────


def _seed_source_file(db, sid: str, name: str, data: bytes) -> Path:
    """模拟 uploadfiles/{file_id}_{原名} 源文件 + session_files 记录。"""
    import os

    upload_dir = Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser" / "uploadfiles"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved = upload_dir / f"ab12cd34_{name}"
    saved.write_bytes(data)
    db.add_session_file(
        session_id=sid,
        filename=name,
        file_path=str(saved),
        file_type=name.rsplit(".", 1)[-1],
        size=len(data),
        username="testuser",
    )
    return saved


def test_ensure_reparses_failed_file_from_uploadfiles(client, db, monkeypatch):
    """首次解析失败占位后提问：ensure_parsed_text 到 uploadfiles 重解析源文件并回填。"""
    sid = _create_session(client)
    monkeypatch.setattr(
        su,
        "parse_file_content",
        lambda _p: "[PDF文件: x.pdf，未安装PDF解析库，请安装: uv pip install pymupdf]",
    )
    _upload(client, sid, "业绩.pdf", b"%PDF-1.4", "application/pdf")
    # 确认首解析确实失败
    assert "解析失败" in su.read_manifest(sid, "testuser", db)

    # "解析库修复"后用户提问
    real_text = "第三季度营收 12.3 亿元，同比增长 8%。"
    monkeypatch.setattr(su, "parse_file_content", lambda _p: real_text)
    _seed_source_file(db, sid, "业绩.pdf", b"%PDF-1.4 fake-bytes")

    parsed = su.ensure_parsed_text(sid, "testuser", "业绩.pdf", db)

    assert real_text in parsed["text"]
    assert parsed["reparsed"] is True
    assert parsed["source_rel"].startswith("uploadfiles/")
    # 正文已回填工作区，清单状态已更新（存储名规则：{去扩展名}.txt）
    stored = _ws_dir(db, sid) / "uploads" / "业绩.txt"
    assert real_text in stored.read_text(encoding="utf-8")
    manifest = su.read_manifest(sid, "testuser", db)
    assert "重解析回填" in manifest
    assert "uploadfiles/" in manifest
    assert "解析失败" not in manifest.split("\n", 1)[-1] or "已解析" in manifest


def test_ensure_skips_reparse_when_text_already_ok(client, db, monkeypatch):
    """正文已可用时不重复解析。"""
    sid = _create_session(client)
    _upload(client, sid, "ok.txt", "正常内容".encode("utf-8"))
    calls = []
    monkeypatch.setattr(
        su, "parse_file_content", lambda _p: calls.append(1) or "不应被调用"
    )

    parsed = su.ensure_parsed_text(sid, "testuser", "ok.txt", db)

    assert "正常内容" in parsed["text"]
    assert parsed["reparsed"] is False
    assert not calls


def test_ensure_reports_failure_when_reparse_still_fails(client, db, monkeypatch):
    """重解析仍失败：返回空正文 + 失败状态（调用方如实告知用户）。"""
    sid = _create_session(client)
    monkeypatch.setattr(
        su,
        "parse_file_content",
        lambda _p: "[PDF文件: x.pdf，未安装PDF解析库，请安装: uv pip install pymupdf]",
    )
    _upload(client, sid, "bad.pdf", b"%PDF-1.4", "application/pdf")
    _seed_source_file(db, sid, "bad.pdf", b"%PDF-1.4 fake")

    parsed = su.ensure_parsed_text(sid, "testuser", "bad.pdf", db)

    assert parsed["text"] == ""
    assert "解析失败" in parsed["status"]


def test_preview_skips_failure_placeholder_but_recovers(client, db, monkeypatch):
    """预览不注入失败占位；解析恢复后预览携带真实正文。"""
    sid = _create_session(client)
    monkeypatch.setattr(
        su,
        "parse_file_content",
        lambda _p: "[PDF文件: x.pdf，未安装PDF解析库，请安装: uv pip install pymupdf]",
    )
    _upload(client, sid, "p2.pdf", b"%PDF-1.4", "application/pdf")

    assert su.build_turn_preview(sid, "testuser", [{"filename": "p2.pdf"}], db) == ""

    monkeypatch.setattr(su, "parse_file_content", lambda _p: "重试后的真实内容")
    _seed_source_file(db, sid, "p2.pdf", b"%PDF-1.4 fake")
    preview = su.build_turn_preview(sid, "testuser", [{"filename": "p2.pdf"}], db)
    assert "重试后的真实内容" in preview
    assert "未安装" not in preview


def test_read_uploaded_file_tool_reparses_and_labels_source(client, db, monkeypatch):
    """工具：重解析成功后返回正文，且来源表述指向用户上传的原始文件而非内部 txt。"""
    import asyncio

    from easy_agent.tools.session_file_reader import ReadUploadedFileTool

    sid = _create_session(client)
    monkeypatch.setattr(
        su,
        "parse_file_content",
        lambda _p: "[PDF文件: x.pdf，未安装PDF解析库，请安装: uv pip install pymupdf]",
    )
    _upload(client, sid, "tool.pdf", b"%PDF-1.4", "application/pdf")
    monkeypatch.setattr(su, "parse_file_content", lambda _p: "工具重解析后的正文")
    _seed_source_file(db, sid, "tool.pdf", b"%PDF-1.4 fake")

    tool = ReadUploadedFileTool(username="testuser", session_id=sid)
    out = asyncio.run(tool._arun("tool.pdf"))

    assert "工具重解析后的正文" in out
    assert "重新解析成功" in out
    assert "您上传的文件" in out or "用户上传" in out
    # 不向模型/用户暴露内部解析文件路径 uploads/*（提示：全文检索说明除外，本例未截断）
    assert "uploads/" not in out
    # 清单已被工具重解析回填
    assert "重解析回填" in su.read_manifest(sid, "testuser", db)


def test_read_uploaded_file_tool_reports_unreadable(client, db, monkeypatch):
    """工具：重解析仍失败时返回明确的"无法提供正文"话术，不编造内容。"""
    import asyncio

    from easy_agent.tools.session_file_reader import ReadUploadedFileTool

    sid = _create_session(client)
    monkeypatch.setattr(
        su,
        "parse_file_content",
        lambda _p: "[PDF文件: x.pdf，未安装PDF解析库，请安装: uv pip install pymupdf]",
    )
    _upload(client, sid, "dead.pdf", b"%PDF-1.4", "application/pdf")

    tool = ReadUploadedFileTool(username="testuser", session_id=sid)
    out = asyncio.run(tool._arun("dead.pdf"))

    assert "无法提供正文" in out
    assert "不要编造" in out


def test_agent_manager_injects_read_tool(client, db, monkeypatch):
    """file_context 启用时，会话 Agent 应注入 read_uploaded_file 工具。"""
    import asyncio

    import easy_agent.agent.core as agent_mod
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")

    sid = _create_session(client)
    captured: dict = {}
    monkeypatch.setattr(
        agent_mod,
        "create_deep_agent",
        lambda **kwargs: (captured.update(kwargs), {"fake": True})[1],
    )

    asyncio.run(
        am.get_or_create_agent_for_session(sid, username="testuser", enable_hitl=False)
    )

    tool_names = [
        getattr(t, "name", "") for t in (captured.get("tools") or [])
    ]
    assert "read_uploaded_file" in tool_names


def test_agent_loads_manifest_as_memory(client, db, monkeypatch):
    """端到端契约：上传文件后创建 Agent，清单会被作为记忆文件传给 DeepAgents。

    这是本方案的核心——清单进 memory → 每轮都在 system prompt，所以第二轮起
    模型依然知道用户传过哪些文件（改造前只有首轮带文件时才注入全文）。

    注意：``client`` 夹具把 ``get_user_workspace_dir`` 重定向到
    ``{TEST_WORKSPACE_DIR}/users/{username}``，比 ``agent.py`` 直接按
    ``{workspace_dir}/{username}`` 拼路径多了一层 ``users/``。生产环境两者一致
    （见 ``delete_session`` 也按该口径删工作区），故这里按生产口径重新打桩，
    以便真正校验"写入位置 == Agent 读取位置"。
    """
    import os

    import easy_agent.agent.core as agent_mod
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")

    monkeypatch.setattr(
        Config,
        "get_user_workspace_dir",
        staticmethod(
            lambda username, config=None: Path(os.environ["TEST_WORKSPACE_DIR"]) / username
        ),
    )

    sid = _create_session(client)
    _upload(client, sid, "memo.txt", "记忆注入验证".encode("utf-8"))

    captured: dict = {}
    monkeypatch.setattr(
        agent_mod,
        "create_deep_agent",
        lambda **kwargs: (captured.update(kwargs), {"fake": True})[1],
    )

    session = db.get_session(sid)
    agent = agent_mod.EasyAgent(
        config=am._agent_config["config"],
        system_prompt=am._agent_config.get("system_prompt", ""),
        skills_root=am._agent_config.get("skills_root", ""),
        username="testuser",
        session_id=sid,
        workspace_name=session.workspace_name,
        mcp_tools=[],
        enable_hitl=False,
    )

    # 写入位置 == Agent 工作区（模型的文件工具就在这个根目录下检索）
    assert (agent.workspace_dir / su.UPLOADS_DIR_NAME / "memo.txt").exists()
    assert (agent.workspace_dir / su.UPLOADS_DIR_NAME / su.MANIFEST_FILENAME).exists()

    memory = captured.get("memory") or []
    manifest_virtual = f"/workspace/{su.UPLOADS_DIR_NAME}/{su.MANIFEST_FILENAME}"
    assert manifest_virtual in memory, f"清单未作为记忆注入：{memory}"


