"""Shell-command sandboxing for agent ``execute`` calls.

DeepAgents' :class:`~deepagents.backends.LocalShellBackend` runs shell commands
directly on the host with **no isolation** (its docstring states this explicitly).
This module wraps each command in a `bubblewrap <https://github.com/containers/bubblewrap>`_
container so the agent can only access:

* the workspace, user-skills and configured external directories (read-write),
* the Python environment that runs the backend (read-only, so ``python`` and
  its installed packages keep working),
* standard system directories (read-only, for shell commands & shared libs).

Everything else on the host (``/home``, ``/root``, ``/var``, the application
source, ``config.yaml`` ...) is invisible and cannot be read or written.

When ``bwrap`` is unavailable (e.g. a minimal Docker image lacking user
namespace support) execution falls back to a best-effort **path allowlist**
that rejects commands referencing host paths outside the allowed roots.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from deepagents.backends.protocol import ExecuteResponse

logger = logging.getLogger(__name__)

# Host directories mounted read-only inside the sandbox so that shell commands,
# shared libraries (the dynamic linker!), SSL certificates and DNS resolution
# keep working.  These contain no user secrets - ``config.yaml`` lives in the
# project root, which is NOT mounted.
_SANDBOX_RO_DIRS: tuple[str, ...] = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt",
)

# Paths considered safe to reference by the best-effort fallback even when they
# are not under an allowed root (commands, libraries, temp devices).
_FALLBACK_SAFE_PREFIXES: tuple[str, ...] = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt",
    "/tmp", "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
)

# Absolute path tokens in a command string.  The leading negative lookbehind
# avoids matching separators inside words (``a/b``) and protocol slashes
# (``http://``).  Path segments never contain ``/``.
_ABS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9._@/:-])/(?:[A-Za-z0-9._@+~-]+/)*[A-Za-z0-9._@+~-]*"
)

_bwrap_usable: bool | None = None


def _venv_prefix() -> str | None:
    """Return the active virtual-env root (read-only mount target) or ``None``.

    Only returns a path when a venv is actually active (``sys.prefix !=
    sys.base_prefix``) and it is not already a system directory.  This prevents
    accidentally bind-mounting the user's home directory when running with a
    system interpreter.
    """
    if sys.prefix == sys.base_prefix:
        return None
    try:
        prefix = str(Path(sys.prefix).resolve())
    except Exception:  # noqa: BLE001
        return None
    if any(prefix == d or prefix.startswith(d + "/") for d in _SANDBOX_RO_DIRS):
        return None
    return prefix


def bwrap_usable() -> bool:
    """Return ``True`` if ``bwrap`` is installed and can create an isolated sandbox.

    The result is cached after the first probe.
    """
    global _bwrap_usable
    if _bwrap_usable is not None:
        return _bwrap_usable
    bwrap = shutil.which("bwrap")
    if not bwrap:
        _bwrap_usable = False
        logger.info("[sandbox] bwrap 未安装，shell 命令降级为路径白名单检测")
        return False
    argv: list[str] = [
        bwrap, "--unshare-all", "--share-net",
        "--die-with-parent", "--new-session",
    ]
    for d in _SANDBOX_RO_DIRS:
        argv += ["--ro-bind-try", d, d]
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "/usr/bin/true"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        _bwrap_usable = proc.returncode == 0
    except Exception as exc:  # noqa: BLE001
        _bwrap_usable = False
        logger.warning("[sandbox] bwrap 探测异常: %s", exc)
    if not _bwrap_usable:
        logger.warning(
            "[sandbox] bwrap 不可用（可能缺少用户命名空间权限），"
            "shell 命令降级为路径白名单检测（best-effort，建议启用 bwrap 获得完整隔离）"
        )
    return _bwrap_usable


def run_sandboxed(
    command: str,
    *,
    allowed_rw_dirs: list[str],
    cwd: str,
    env: dict[str, str] | None,
    timeout: int,
    max_output_bytes: int,
) -> ExecuteResponse:
    """Run *command* inside a bwrap sandbox and return an :class:`ExecuteResponse`.

    Mirrors the output handling of ``LocalShellBackend.execute`` (stderr is
    prefixed with ``[stderr]``, output is truncated at *max_output_bytes*, and a
    non-zero exit code is appended).
    """
    argv: list[str] = [
        "bwrap", "--unshare-all", "--share-net",
        "--die-with-parent", "--new-session",
    ]
    # read-only system dirs (commands + shared libraries)
    for d in _SANDBOX_RO_DIRS:
        argv += ["--ro-bind-try", d, d]
    # read-only python environment so `python` + installed packages keep working
    venv = _venv_prefix()
    if venv:
        argv += ["--ro-bind-try", venv, venv]
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    # read-write: workspace, skills, external dirs (bound at their real path so
    # virtual->real path translation inside the command keeps working)
    for d in allowed_rw_dirs:
        argv += ["--bind", d, d]
    # HOME -> tmpfs so apps have a writable home without exposing the real one
    argv += ["--setenv", "HOME", "/tmp"]
    argv += ["sh", "-c", f"cd {shlex.quote(cwd)} && {command}"]

    try:
        result = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            check=False,
            shell=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return ExecuteResponse(
            output=f"Error: Command timed out after {timeout} seconds.",
            exit_code=124,
            truncated=False,
        )
    except FileNotFoundError:
        return ExecuteResponse(
            output="Error: bwrap executable not found.",
            exit_code=127,
            truncated=False,
        )

    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout)
    if result.stderr:
        parts.extend(
            f"[stderr] {line}" for line in result.stderr.strip().split("\n")
        )
    output = "\n".join(parts) if parts else "<no output>"
    truncated = False
    if len(output) > max_output_bytes:
        output = output[:max_output_bytes]
        output += f"\n\n... Output truncated at {max_output_bytes} bytes."
        truncated = True
    if result.returncode != 0:
        output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
    return ExecuteResponse(
        output=output, exit_code=result.returncode, truncated=truncated
    )


def check_command_paths(command: str, allowed_rw_dirs: list[str]) -> str | None:
    """Best-effort path allowlist for the no-bwrap fallback.

    Returns an error message if *command* references an absolute host path
    outside the allowed roots / safe system prefixes, otherwise ``None``.
    """
    safe = {p.rstrip("/") for p in _FALLBACK_SAFE_PREFIXES}
    venv = _venv_prefix()
    if venv:
        safe.add(venv.rstrip("/"))
    for d in allowed_rw_dirs:
        safe.add(d.rstrip("/"))
    safe_sorted = sorted(safe, key=len, reverse=True)

    for m in _ABS_PATH_RE.finditer(command):
        path = m.group(0)
        if _path_allowed(path, safe_sorted):
            continue
        return (
            "Error: 命令引用了工作区之外的路径 "
            f"{path}，已被沙箱拒绝。\n"
            "智能体仅允许访问当前工作区及已授权目录，请使用工作区内路径或虚拟路径。"
        )
    # block home-directory shortcut expansions (~/...)
    if re.search(r"(^|[\s;|&()=])~/", command):
        return (
            "Error: 命令使用了 ~ (用户主目录) 路径引用，已被沙箱拒绝。"
            "请使用工作区内路径。"
        )
    return None


def _path_allowed(path: str, safe_prefixes: list[str]) -> bool:
    p = path.rstrip("/")
    if not p:
        return False  # bare "/" -> filesystem root, never allowed
    for prefix in safe_prefixes:
        if p == prefix or p.startswith(prefix + "/"):
            return True
    return False
