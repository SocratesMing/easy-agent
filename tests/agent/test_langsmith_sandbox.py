"""Sandbox B：可选 LangSmith Sandbox 远端执行后端（fake backend，不联网）。"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from deepagents.backends import CompositeBackend
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    SandboxBackendProtocol,
)
from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _all_paths_scoped_to_routes,
    supports_execution,
)

from easy_agent.agent import backend as agent_backend_mod
from easy_agent.agent import _build_backend_for_context
from easy_agent.agent.graph_cache import SessionContext
from easy_agent.backends.context import ContextBackend
from easy_agent.backends.langsmith_sandbox import (
    LangSmithExecuteShell,
    create_langsmith_backend,
    sandbox_mode,
)
from easy_agent.backends.shell import PathTranslatingShell
from easy_agent.bootstrap.config import Config


class FakeSandbox:
    """最小 LangSmithSandbox 替身：内存文件表 + 命令记录。"""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.upload_calls: list[list[tuple[str, bytes]]] = []
        self.executed: list[tuple[str, int | None]] = []
        self.mkdir_calls: list[tuple[str, int | None]] = []
        self.download_calls: list[list[str]] = []
        self.response = ExecuteResponse(
            output="remote-ok", exit_code=0, truncated=False
        )
        self.upload_error: Exception | None = None
        self.command_error: Exception | None = None
        self.on_execute = None

    def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        if command.startswith("mkdir -p "):
            self.mkdir_calls.append((command, timeout))
            return ExecuteResponse(output="", exit_code=0, truncated=False)
        self.executed.append((command, timeout))
        if self.command_error is not None:
            raise self.command_error
        if self.on_execute is not None:
            self.on_execute(command, self)
        return self.response

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        self.upload_calls.append(list(files))
        if self.upload_error is not None:
            raise self.upload_error
        responses: list[FileUploadResponse] = []
        for path, content in files:
            self.files[path] = content
            responses.append(FileUploadResponse(path=path, error=None))
        return responses

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(
            matches=[
                {"path": item, "is_dir": False} for item in sorted(self.files)
            ]
        )

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.download_calls.append(list(paths))
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if path in self.files:
                responses.append(
                    FileDownloadResponse(
                        path=path, content=self.files[path], error=None
                    )
                )
            else:
                responses.append(
                    FileDownloadResponse(
                        path=path, content=None, error="file_not_found"
                    )
                )
        return responses


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path


def _backend(
    workspace: Path,
    fake: FakeSandbox,
    monkeypatch,
    *,
    workspace_virtual_path: str = "/workspace",
    remote_root: str | None = None,
    max_sync_mb: float | None = None,
) -> LangSmithExecuteShell:
    monkeypatch.delenv("EASY_SANDBOX_SYNC_MAX_MB", raising=False)
    monkeypatch.delenv("EASY_SANDBOX_REMOTE_ROOT", raising=False)
    kwargs = {}
    if max_sync_mb is not None:
        kwargs["max_sync_mb"] = max_sync_mb
    return create_langsmith_backend(
        workspace_dir=workspace,
        path_mappings={f"{workspace_virtual_path}/": str(workspace) + "/"},
        workspace_virtual_path=workspace_virtual_path,
        remote_root=remote_root,
        sandbox_factory=lambda: fake,
        sid="test",
        **kwargs,
    )


def _ctx(tmp_path: Path) -> SessionContext:
    workspace = tmp_path / "workspace" / "u1" / "s1"
    skills = tmp_path / "workspace" / "u1" / "skills"
    return SessionContext(
        session_id="s1",
        username="u1",
        safe_username="u1",
        workspace_dir=str(workspace),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(skills),
        memory_file=str(workspace / "memory.md"),
        long_term_memory_file=str(tmp_path / "memories" / "u1" / "AGENTS.md"),
        workspace_name="w1",
    )


# ── 配置/工厂 ────────────────────────────────────────────────


def test_sandbox_mode_defaults_to_local(monkeypatch):
    monkeypatch.delenv("EASY_SANDBOX", raising=False)
    assert sandbox_mode() == "local"

    monkeypatch.setenv("EASY_SANDBOX", " LANGSMITH ")
    assert sandbox_mode() == "langsmith"

    monkeypatch.setenv("EASY_SANDBOX", "local")
    assert sandbox_mode() == "local"

    monkeypatch.setenv("EASY_SANDBOX", "external")
    with pytest.raises(ValueError, match="EASY_SANDBOX"):
        sandbox_mode()


def test_factory_requires_langsmith_api_key(workspace, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LANGSMITH_API_KEY"):
        create_langsmith_backend(
            workspace_dir=workspace,
            path_mappings={"/workspace/": str(workspace) + "/"},
        )


def test_factory_wraps_sandbox_creation_failure(workspace):
    def boom():
        raise RuntimeError("create boom")

    with pytest.raises(RuntimeError, match="sandbox_factory") as excinfo:
        create_langsmith_backend(
            workspace_dir=workspace,
            path_mappings={"/workspace/": str(workspace) + "/"},
            sandbox_factory=boom,
        )
    assert "create boom" in str(excinfo.value)


def test_factory_with_injected_fake_does_not_need_credentials(
    workspace, monkeypatch
):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    fake = FakeSandbox()
    backend = _backend(workspace, fake, monkeypatch)
    assert isinstance(backend, LangSmithExecuteShell)
    assert isinstance(backend, PathTranslatingShell)


# ── execute：同步上传 / 远端执行 / 回写 ───────────────────────


def test_execute_uploads_runs_remote_and_downloads(workspace, monkeypatch):
    (workspace / "input.txt").write_text("hello", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "nested.txt").write_bytes(b"nested")
    (workspace / "local-extra.txt").write_text("keep", encoding="utf-8")
    fake = FakeSandbox()

    def after(command, sandbox):
        sandbox.files["/workspace/out.txt"] = b"generated"

    fake.on_execute = after
    backend = _backend(workspace, fake, monkeypatch)

    response = backend.execute("cat input.txt && python gen.py", timeout=7)

    assert response is fake.response
    assert fake.executed == [
        ("cd /workspace && cat input.txt && python gen.py", 7)
    ]
    uploaded = dict(fake.upload_calls[-1])
    assert uploaded["/workspace/input.txt"] == b"hello"
    assert uploaded["/workspace/sub/nested.txt"] == b"nested"
    assert "/workspace/local-extra.txt" in uploaded
    assert fake.mkdir_calls[0][0].startswith("mkdir -p /workspace")
    assert "/workspace/sub" in fake.mkdir_calls[0][0]
    assert (workspace / "out.txt").read_bytes() == b"generated"
    # 不删除远端不存在的本地额外文件。
    assert (workspace / "local-extra.txt").read_text(encoding="utf-8") == "keep"
    assert any("/workspace/out.txt" in batch for batch in fake.download_calls)


def test_first_upload_failure_blocks_execution(workspace, monkeypatch):
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    fake = FakeSandbox()
    fake.upload_error = RuntimeError("upload boom")
    backend = _backend(workspace, fake, monkeypatch)

    response = backend.execute("echo hi")

    assert response.exit_code == 1
    assert "拒绝执行" in response.output
    assert "upload boom" in response.output
    assert fake.executed == []


def test_later_upload_failure_warns_but_still_executes(
    workspace, monkeypatch, caplog
):
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    fake = FakeSandbox()
    backend = _backend(workspace, fake, monkeypatch)

    first = backend.execute("echo first")
    assert first.exit_code == 0

    fake.upload_error = RuntimeError("later boom")
    with caplog.at_level(logging.WARNING):
        second = backend.execute("echo second")

    assert second is fake.response
    assert ("cd /workspace && echo second", None) in fake.executed
    assert any("later boom" in record.message for record in caplog.records)


def test_remote_execute_error_propagates(workspace, monkeypatch):
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    fake = FakeSandbox()
    fake.command_error = RuntimeError("remote boom")
    backend = _backend(workspace, fake, monkeypatch)

    with pytest.raises(RuntimeError, match="remote boom"):
        backend.execute("echo hi")


def test_command_is_wrapped_and_virtual_prefix_is_mapped(workspace, monkeypatch):
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    fake = FakeSandbox()
    backend = _backend(
        workspace,
        fake,
        monkeypatch,
        workspace_virtual_path="/proj",
    )

    backend.execute("cat /proj/a.txt")

    assert fake.executed == [("cd /workspace && cat /workspace/a.txt", None)]


def test_custom_remote_root_is_used_for_cd_and_mapping(workspace, monkeypatch):
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    fake = FakeSandbox()
    backend = _backend(
        workspace,
        fake,
        monkeypatch,
        remote_root="/remote/ws",
    )

    backend.execute("cat /workspace/a.txt")

    assert fake.executed == [
        ("cd /remote/ws && cat /remote/ws/a.txt", None)
    ]


def test_sync_excludes_dependency_dirs_and_large_files(workspace, monkeypatch):
    for name in (".deps", ".venv", "node_modules", "__pycache__"):
        directory = workspace / name
        directory.mkdir()
        (directory / "x.txt").write_text("x", encoding="utf-8")
    (workspace / "small.txt").write_text("small", encoding="utf-8")
    (workspace / "large.bin").write_bytes(b"x" * 2048)

    fake = FakeSandbox()
    backend = _backend(workspace, fake, monkeypatch, max_sync_mb=0.001)
    backend.execute("true")

    uploaded = {path for path, _ in fake.upload_calls[-1]}
    assert "/workspace/small.txt" in uploaded
    assert "/workspace/large.bin" not in uploaded
    for excluded in (".deps", ".venv", "node_modules", "__pycache__"):
        assert f"/workspace/{excluded}/x.txt" not in uploaded


async def test_aexecute_runs_sync_protocol(workspace, monkeypatch):
    (workspace / "a.txt").write_text("a", encoding="utf-8")
    fake = FakeSandbox()
    backend = _backend(workspace, fake, monkeypatch)

    response = await backend.aexecute("echo hi", timeout=3)

    assert response is fake.response
    assert fake.executed == [("cd /workspace && echo hi", 3)]


# ── agent 接入 / 本地模式回归 ────────────────────────────────


def test_local_mode_agent_backend_stays_local(tmp_path, monkeypatch):
    monkeypatch.delenv("EASY_SANDBOX", raising=False)
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    config.agent.memories_dir = str(tmp_path / "memories")
    context = _ctx(tmp_path)

    backend = _build_backend_for_context(config, context, [])

    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, PathTranslatingShell)
    assert not isinstance(backend.default, LangSmithExecuteShell)
    assert set(backend.routes) == {
        "/workspace/",
        "/memories/",
        "/user-skills/",
    }


def test_langsmith_mode_uses_remote_default_and_keeps_routes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EASY_SANDBOX", "langsmith")
    sentinel = object()
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        agent_backend_mod, "create_langsmith_backend", fake_create
    )
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    config.agent.memories_dir = str(tmp_path / "memories")
    context = _ctx(tmp_path)

    backend = _build_backend_for_context(config, context, [])

    assert backend.default is sentinel
    assert set(backend.routes) == {
        "/workspace/",
        "/memories/",
        "/user-skills/",
    }
    assert captured["workspace_dir"] == Path(context.workspace_dir).absolute()
    assert captured["workspace_virtual_path"] == "/workspace"
    assert captured["path_mappings"]["/workspace/"] == (
        str(Path(context.workspace_dir).absolute()) + "/"
    )


# ── ContextBackend 命令转发（D2 共享图前置修复） ─────────────


class _StubComposite:
    def __init__(self) -> None:
        self.commands: list[tuple[str, int | None]] = []

    def execute(self, command, *, timeout=None):
        self.commands.append((command, timeout))
        return ExecuteResponse(output="stub", exit_code=0, truncated=False)

    async def aexecute(self, command, *, timeout=None):
        self.commands.append((command, timeout))
        return ExecuteResponse(output="async-stub", exit_code=0, truncated=False)


def test_context_backend_proxies_execute_through_current_backend():
    stub = _StubComposite()
    backend = ContextBackend(lambda _ctx: stub, fallback_context=_ctx(Path("/tmp")))

    assert isinstance(backend, SandboxBackendProtocol)
    assert backend.execute("echo hi", timeout=5).output == "stub"
    assert stub.commands == [("echo hi", 5)]


async def test_context_backend_proxies_aexecute_through_current_backend():
    stub = _StubComposite()
    backend = ContextBackend(lambda _ctx: stub, fallback_context=_ctx(Path("/tmp")))

    response = await backend.aexecute("echo async", timeout=2)

    assert response.output == "async-stub"
    assert stub.commands == [("echo async", 2)]


def test_context_backend_satisfies_execute_permission_gate():
    """带 permissions 时，ContextBackend 仍能通过框架的 execute 门禁。"""
    stub = _StubComposite()
    backend = ContextBackend(
        lambda _ctx: stub,
        fallback_context=_ctx(Path("/tmp")),
        route_prefixes=["/workspace/", "/memories/", "/user-skills/"],
    )
    permission = FilesystemPermission(
        operations=["write"], paths=["/user-skills/**"], mode="deny"
    )

    assert isinstance(backend, CompositeBackend)
    assert supports_execution(backend) is True
    assert _all_paths_scoped_to_routes([permission], backend) is True
    assert set(backend.routes) == {"/workspace/", "/memories/", "/user-skills/"}
    assert backend.sorted_routes == []