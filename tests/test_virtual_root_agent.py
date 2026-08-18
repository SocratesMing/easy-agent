from types import SimpleNamespace
from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.middleware.skills import SkillsMiddleware
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from tests.virtual_root_agent import (
    build_agent,
    build_backend,
    InteractiveStreamPrinter,
    resolve_skills_dir,
)


def test_backend_allows_operations_inside_virtual_root(tmp_path):
    workspace = tmp_path / "session"
    workspace.mkdir()
    backend = build_backend(workspace)

    backend.write("/allowed.txt", "session secret")

    assert backend.read("/allowed.txt").file_data["content"] == "session secret"
    assert (workspace / "allowed.txt").read_text(encoding="utf-8") == "session secret"


def test_backend_rejects_traversal_outside_virtual_root(tmp_path):
    workspace = tmp_path / "session"
    workspace.mkdir()
    backend = build_backend(workspace)

    with pytest.raises(ValueError, match="Path traversal not allowed"):
        backend.write("/../outside.txt", "escaped")

    assert not (tmp_path / "outside.txt").exists()


def test_backend_virtualizes_absolute_paths_inside_root(tmp_path):
    workspace = tmp_path / "session"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("host secret", encoding="utf-8")
    backend = build_backend(workspace)

    initial_read = backend.read("/outside.txt")
    assert initial_read.error is not None
    assert initial_read.file_data is None

    backend.write("/outside.txt", "session value")
    assert (workspace / "outside.txt").read_text(encoding="utf-8") == "session value"
    assert (tmp_path / "outside.txt").read_text(encoding="utf-8") == "host secret"


def test_backend_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "session"
    workspace.mkdir()
    host_secret = tmp_path / "host-secret.txt"
    host_secret.write_text("host secret", encoding="utf-8")
    (workspace / "escape.link").symlink_to(host_secret)
    backend = build_backend(workspace)

    with pytest.raises(ValueError, match="outside root directory"):
        backend.read("/escape.link")


def test_backend_maps_workspace_and_skills_to_distinct_directories(tmp_path):
    workspace = tmp_path / "session"
    skills_dir = tmp_path / "skills"
    workspace.mkdir()
    skills_dir.mkdir()
    backend = build_backend(workspace, skills_dir=skills_dir)

    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, LocalShellBackend)
    assert isinstance(backend.routes["/skills/"], LocalShellBackend)
    assert backend.default.cwd == workspace.resolve()
    assert backend.routes["/skills/"].cwd == skills_dir.resolve()

    backend.write("/allowed.txt", "workspace-data")
    backend.write("/skills/allowed.txt", "skills-data")

    assert (workspace / "allowed.txt").read_text(encoding="utf-8") == "workspace-data"
    assert (skills_dir / "allowed.txt").read_text(encoding="utf-8") == "skills-data"


def test_backend_executes_shell_commands_in_workspace(tmp_path):
    workspace = tmp_path / "session"
    workspace.mkdir()
    backend = build_backend(workspace)

    result = backend.execute("pwd && printf 'shell-data' > allowed.txt")

    assert result.exit_code == 0
    assert str(workspace.resolve()) in result.output
    assert (workspace / "allowed.txt").read_text(encoding="utf-8") == "shell-data"


def test_backend_parses_skills_from_distinct_route(tmp_path):
    skills_dir = tmp_path / "skills"
    (skills_dir / "demo-skill").mkdir(parents=True)
    (skills_dir / "demo-skill" / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demonstrates skill parsing\n"
        "---\n"
        "# Demo Skill\n",
        encoding="utf-8",
    )
    backend = build_backend(tmp_path / "session", skills_dir=skills_dir)

    assert isinstance(backend, CompositeBackend)
    response = backend.download_files(["/skills/demo-skill/SKILL.md"])[0]
    assert response.error is None
    assert b"Demonstrates skill parsing" in response.content

    middleware = SkillsMiddleware(backend=backend, sources=["/skills/"])
    update = middleware.before_agent({}, SimpleNamespace(), {})
    metadata = update["skills_metadata"]
    assert [skill["name"] for skill in metadata] == ["demo-skill"]
    assert metadata[0]["description"] == "Demonstrates skill parsing"


def test_build_agent_uses_shell_backend_and_skills_source(tmp_path):
    workspace = tmp_path / "session"
    skills_dir = tmp_path / "skills"
    workspace.mkdir()
    skills_dir.mkdir()
    captured = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return "compiled-agent"

    import tests.virtual_root_agent as module
    original_create_deep_agent = module.create_deep_agent
    module.create_deep_agent = fake_create_deep_agent
    try:
        agent = build_agent(
            workspace=workspace,
            skills_dir=skills_dir,
            model="fake-model",
            session_name="test-session",
        )
    finally:
        module.create_deep_agent = original_create_deep_agent

    assert agent == "compiled-agent"
    assert captured["model"] == "fake-model"
    assert isinstance(captured["backend"], CompositeBackend)
    assert captured["backend"].default.cwd == workspace.resolve()
    assert captured["backend"].routes["/skills/"].cwd == skills_dir.resolve()
    assert captured["skills"] == ["/skills/"]


def test_resolve_skills_dir_uses_configured_dev_directory():
    config = SimpleNamespace(tools=SimpleNamespace(skills_dir="/qts/skills"))

    assert resolve_skills_dir(config) == Path("/qts/skills")
    assert resolve_skills_dir(config, "/custom/skills") == Path("/custom/skills")


def test_stream_printer_shows_reasoning_tool_call_result_and_answer(capsys):
    printer = InteractiveStreamPrinter()

    printer.handle(
        "messages",
        (
            AIMessageChunk(
                content="",
                additional_kwargs={"reasoning_content": "需要先读取文件"},
            ),
            {},
        ),
    )
    printer.handle(
        "updates",
        {
            "model": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "read_file",
                                "args": {"file_path": "/allowed.txt"},
                                "id": "call-1",
                            }
                        ],
                    )
                ]
            }
        },
    )
    printer.handle(
        "updates",
        {
            "tools": {
                "messages": [
                    ToolMessage(
                        content="session-only",
                        name="read_file",
                        tool_call_id="call-1",
                    )
                ]
            }
        },
    )
    printer.handle(
        "messages",
        (AIMessageChunk(content="文件内容是 session-only"), {}),
    )
    printer.handle(
        "updates",
        {"model": {"messages": [AIMessage(content="文件内容是 session-only")]}},
    )

    output = capsys.readouterr().out
    assert "🧠 推理过程" in output
    assert "需要先读取文件" in output
    assert "🔧 工具调用: read_file" in output
    assert '"file_path": "/allowed.txt"' in output
    assert "📥 工具结果: read_file" in output
    assert "session-only" in output
    assert "✅ 正式回答" in output
    assert output.count("文件内容是 session-only") == 1
