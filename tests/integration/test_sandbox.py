"""Tests for the shell-command sandbox (``easy_agent.sandboxing.sandbox``).

Covers:
* the best-effort path allowlist used when bwrap is unavailable, and
* end-to-end isolation when bwrap is present (workspace writable, host paths
  like ``/home``, ``/root``, ``config.yaml`` invisible).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import easy_agent.sandboxing.sandbox as sandbox_mod
from easy_agent.sandboxing import landlock as landlock_mod
from easy_agent.sandboxing.sandbox import (
    _path_allowed,
    _venv_prefix,
    bwrap_usable,
    check_command_paths,
    log_sandbox_startup_report,
    sandbox_report,
)
from easy_agent.agent import _PathTranslatingShell

WS_REAL = "/home/sututu/code/easy-agent/workspace/user/sess"


class TestPathAllowlist:
    """Unit tests for the no-bwrap fallback path checker."""

    def test_allows_workspace_paths(self):
        assert check_command_paths(
            f"cat {WS_REAL}/file.txt", [WS_REAL]
        ) is None

    def test_allows_relative_and_system(self):
        assert check_command_paths("python3 script.py", [WS_REAL]) is None
        assert check_command_paths("/usr/bin/python3 -c 'print(1)'", [WS_REAL]) is None
        assert check_command_paths("ls -la", [WS_REAL]) is None

    def test_allows_safe_system_prefixes(self):
        # /etc is ro-bound in bwrap; fallback treats it as safe (matches bwrap)
        assert check_command_paths("cat /etc/passwd", [WS_REAL]) is None
        assert check_command_paths("2>/dev/null", [WS_REAL]) is None

    def test_allows_urls(self):
        assert check_command_paths(
            "pip install https://pypi.org/simple/pkg", [WS_REAL]
        ) is None

    def test_blocks_host_home(self):
        msg = check_command_paths("cat /home/sututu/.ssh/id_rsa", [WS_REAL])
        assert msg is not None and "工作区之外" in msg

    def test_blocks_project_config(self):
        msg = check_command_paths(
            "cat /home/sututu/code/easy-agent/config.yaml", [WS_REAL]
        )
        assert msg is not None

    def test_blocks_root_and_var(self):
        assert check_command_paths("ls /root", [WS_REAL]) is not None
        assert check_command_paths("cat /var/log/syslog", [WS_REAL]) is not None

    def test_blocks_bare_root(self):
        assert check_command_paths("cd /", [WS_REAL]) is not None
        assert check_command_paths("find / -name x", [WS_REAL]) is not None

    def test_blocks_tilde_expansion(self):
        # ~/secret is blocked (the path regex catches "/secret" after ~)
        msg = check_command_paths("cat ~/secret", [WS_REAL])
        assert msg is not None

    def test_path_allowed_helper(self):
        safe = ["/usr", "/home/user/ws"]
        assert _path_allowed("/usr/bin/python3", safe)
        assert _path_allowed("/home/user/ws/x", safe)
        assert _path_allowed("/home/user/ws", safe)
        assert not _path_allowed("/home/user/.ssh", safe)
        assert not _path_allowed("/", safe)
        assert not _path_allowed("/etc", safe) if "/etc" not in safe else True


class TestVenvPrefix:
    def test_returns_none_for_system_or_under_system(self):
        # _venv_prefix only returns a path when a venv is active AND it is not
        # itself under a system dir. We just assert it never returns a bare "/".
        v = _venv_prefix()
        assert v is None or v != "/"


@pytest.mark.skipif(not bwrap_usable(), reason="bwrap not available")
class TestBwrapIsolation:
    """End-to-end: _PathTranslatingShell.execute under bwrap."""

    def _make_shell(self, tmp_ws: Path) -> _PathTranslatingShell:
        (tmp_ws / "hello.txt").write_text("ws content")
        env = os.environ.copy()
        venv = _venv_prefix()
        if venv:
            env["PATH"] = f"{venv}/bin:/usr/bin:/bin"
        return _PathTranslatingShell(
            path_mappings={"/workspace/test/": str(tmp_ws) + "/"},
            root_dir=str(tmp_ws),
            virtual_mode=True,
            inherit_env=False,
            env=env,
            timeout=30,
            sandbox_enabled=True,
        )

    def test_workspace_read_write(self, tmp_path):
        shell = self._make_shell(tmp_path)
        r = shell.execute("cat /workspace/test/hello.txt")
        assert r.exit_code == 0 and "ws content" in r.output
        r = shell.execute("echo created > /workspace/test/out.txt && cat /workspace/test/out.txt")
        assert r.exit_code == 0 and "created" in r.output

    def test_blocks_host_home_and_config(self, tmp_path):
        shell = self._make_shell(tmp_path)
        # /home other than the venv path must be invisible
        r = shell.execute("cat /home/sututu/.bashrc")
        assert r.exit_code != 0
        r = shell.execute("cat /home/sututu/code/easy-agent/config.yaml")
        assert "No such file" in r.output

    def test_blocks_root_and_var(self, tmp_path):
        shell = self._make_shell(tmp_path)
        for path in ("/root", "/var", "/srv"):
            r = shell.execute(f"ls {path}")
            assert r.exit_code != 0, f"{path} should be inaccessible"

    def test_etc_is_readonly(self, tmp_path):
        shell = self._make_shell(tmp_path)
        r = shell.execute("echo x > /etc/sandbox_test_file")
        assert r.exit_code != 0
        assert "Read-only" in r.output or "denied" in r.output

    def test_write_outside_workspace_does_not_reach_host(self, tmp_path):
        shell = self._make_shell(tmp_path)
        # Writing to a synthetic bwrap parent dir stays in the sandbox tmpfs.
        shell.execute("echo pwned > /home/sututu/code/easy-agent/pwned_file")
        assert not Path("/home/sututu/code/easy-agent/pwned_file").exists()

    def test_system_tools_work(self, tmp_path):
        shell = self._make_shell(tmp_path)
        r = shell.execute("node --version")
        # node may not be installed in every env; only assert when present
        node = shutil.which("node")
        if node:
            assert r.exit_code == 0 and "v" in r.output


class _FakeCompleted:
    """Minimal ``subprocess.CompletedProcess`` stand-in for the probe."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    def _run(argv, *args, **kwargs):  # noqa: ANN001, ARG001
        return _FakeCompleted(returncode, stdout, stderr)

    return _run


class TestSandboxStartupReport:
    """启动期沙箱自检：结论、修复建议与 strict 行为（不依赖真实 bwrap）。"""

    @staticmethod
    def _reset_probe(monkeypatch, landlock_ok: bool) -> None:
        monkeypatch.setattr(sandbox_mod, "_bwrap_usable", None)
        monkeypatch.setattr(sandbox_mod, "_bwrap_probe_path", None)
        monkeypatch.setattr(sandbox_mod, "_bwrap_probe_reason", "")
        monkeypatch.setattr(landlock_mod, "landlock_usable", lambda: landlock_ok)

    def test_bwrap_ok_reports_version(self, monkeypatch):
        self._reset_probe(monkeypatch, landlock_ok=False)
        monkeypatch.setattr(
            sandbox_mod.shutil, "which", lambda _name: "/usr/local/bin/bwrap"
        )
        monkeypatch.setattr(
            sandbox_mod.subprocess,
            "run",
            _fake_run(0, "bubblewrap 0.13.1\n", ""),
        )

        report = sandbox_report(sandbox_enabled=True)

        assert report["mode"] == "bwrap"
        assert report["bwrap_path"] == "/usr/local/bin/bwrap"
        assert report["bwrap_version"] == "bubblewrap 0.13.1"
        assert report["bwrap_ok"] is True
        assert report["hints"] == []

    def test_missing_binary_degrades_to_landlock(self, monkeypatch):
        self._reset_probe(monkeypatch, landlock_ok=True)
        monkeypatch.setattr(sandbox_mod.shutil, "which", lambda _name: None)

        report = sandbox_report(sandbox_enabled=True)

        assert report["mode"] == "landlock"
        assert report["bwrap_ok"] is False
        assert report["landlock_ok"] is True
        assert "找不到 bwrap" in str(report["bwrap_reason"])

    def test_fail_closed_carries_actionable_hints(self, monkeypatch):
        self._reset_probe(monkeypatch, landlock_ok=False)
        monkeypatch.setattr(sandbox_mod.shutil, "which", lambda _name: "/usr/bin/bwrap")
        monkeypatch.setattr(
            sandbox_mod.subprocess,
            "run",
            _fake_run(
                1,
                "",
                "bwrap: Creating new namespace failed: Operation not permitted",
            ),
        )

        report = sandbox_report(sandbox_enabled=True)
        hints = " ".join(str(h) for h in report["hints"])

        assert report["mode"] == "fail-closed"
        assert "Operation not permitted" in str(report["bwrap_reason"])
        assert "max_user_namespaces" in hints
        assert "seccomp=unconfined" in hints
        # 非 strict 只记日志，不阻断启动
        assert log_sandbox_startup_report(sandbox_enabled=True)["mode"] == "fail-closed"

    def test_strict_fails_startup_when_nothing_available(self, monkeypatch):
        self._reset_probe(monkeypatch, landlock_ok=False)
        monkeypatch.setattr(sandbox_mod.shutil, "which", lambda _name: None)

        with pytest.raises(RuntimeError, match="沙箱自检失败"):
            log_sandbox_startup_report(sandbox_enabled=True, strict=True)

    def test_disabled_sandbox_skips_probe(self, monkeypatch):
        self._reset_probe(monkeypatch, landlock_ok=True)
        monkeypatch.setattr(
            sandbox_mod.shutil,
            "which",
            lambda _name: pytest.fail("sandbox_enabled=false 时不应探测 bwrap"),
        )

        report = sandbox_report(sandbox_enabled=False)

        assert report["mode"] == "disabled"
        assert report["bwrap_ok"] is False
        assert "sandbox_enabled=false" in str(report["bwrap_reason"])
