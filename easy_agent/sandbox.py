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
import os
import tempfile
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

# Environment variables safe to forward into the sandbox. Everything else -
# notably ``EASY_JWT_SECRET``, LLM API keys and DB credentials - is stripped so
# the agent cannot exfiltrate server secrets via ``env``, ``printenv`` or
# ``/proc/self/environ``.
_SANITIZED_ENV_KEEP: frozenset[str] = frozenset({
    "PATH", "HOME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_COLLATE",
    "LC_TIME", "LC_NUMERIC", "LC_MONETARY", "LC_MESSAGES", "LC_PAPER",
    "LC_NAME", "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT",
    "LC_IDENTIFICATION", "TZ", "TERM", "SHELL",
})

# Sensitive /etc files masked (overlaid with /dev/null) inside the bwrap
# sandbox so the agent cannot read host credentials or the host username.
_BWRAP_MASKED_ETC_FILES: tuple[str, ...] = (
    "/etc/environment",
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/subuid",
    "/etc/subgid",
)

# Neutral mount point for the Python venv. Binding it here (instead of its real
# host path, which typically lives under ``/home/<user>/...``) keeps ``/home``
# invisible inside the sandbox.
_VENV_MOUNT = "/venv"


def _sanitize_env(
    env: dict[str, str] | None, *, home: str, path: str
) -> dict[str, str]:
    """Return a copy of *env* containing only non-sensitive variables.

    Only a locale/path/timezone whitelist is kept; HOME and PATH are overridden.
    Server secrets are never forwarded to the sandboxed command.
    """
    safe: dict[str, str] = {}
    for key, value in (env or {}).items():
        if key in _SANITIZED_ENV_KEEP or key.startswith("LC_"):
            safe[key] = value
    safe["PATH"] = path
    safe["HOME"] = home
    return safe


def _sandbox_path(venv: str | None) -> str:
    """PATH for the sandboxed command: venv bin (neutral mount) + system dirs."""
    prefix = f"{_VENV_MOUNT}/bin:" if venv else ""
    return f"{prefix}/usr/local/bin:/usr/bin:/bin"


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
    binds: list[tuple[str, str]],
    cwd: str,
    env: dict[str, str] | None,
    timeout: int,
    max_output_bytes: int,
) -> ExecuteResponse:
    """Run *command* inside a bwrap sandbox and return an :class:`ExecuteResponse`.

    Each entry in *binds* is a ``(real_host_dir, sandbox_target)`` pair: the real
    workspace/skills/external directory is mounted (read-write) at its *virtual*
    path inside the sandbox. Binding at the virtual path - rather than the real
    host path, which typically lives under ``/home/<user>/...`` - keeps ``/home``
    and ``/root`` completely invisible. The model uses virtual paths directly,
    so no path translation is needed.

    *cwd* is the virtual working directory used inside the sandbox (via ``cd``).
    """
    argv: list[str] = [
        "bwrap", "--unshare-all", "--share-net",
        "--die-with-parent", "--new-session",
    ]
    # read-only system dirs (commands + shared libraries)
    for d in _SANDBOX_RO_DIRS:
        argv += ["--ro-bind-try", d, d]
    # read-only python environment, bound at a neutral path so the real host
    # venv path (usually under /home) is never exposed.
    venv = _venv_prefix()
    if venv:
        argv += ["--ro-bind-try", venv, _VENV_MOUNT]
    argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    # Mask sensitive /etc files (overlay /dev/null) so the agent cannot read
    # host credentials or the host username via /etc/environment etc.
    for f in _BWRAP_MASKED_ETC_FILES:
        argv += ["--ro-bind-try", "/dev/null", f]
    # Neutral /etc/passwd & /etc/group: overlay minimal files so getpwuid()
    # resolves to a generic "agent" identity instead of leaking the real host
    # username (via `whoami`, `id`, `ls -l` owner fields). The real uid is
    # mapped to "agent" so user-aware commands keep working.
    _uid = os.getuid()
    _etc_overlays = {
        "/etc/passwd": (
            "root:x:0:0:root:/root:/bin/sh\n"
            f"agent:x:{_uid}:{_uid}:agent:/tmp:/bin/sh\n"
            "nobody:x:65534:65534:nobody:/:/usr/sbin/nologin\n"
        ),
        "/etc/group": (
            "root:x:0:\n"
            f"agent:x:{_uid}:\n"
            "nogroup:x:65534:\n"
        ),
    }
    _etc_tmp: list[str] = []
    for _target, _content in _etc_overlays.items():
        _fd, _path = tempfile.mkstemp(prefix="sandbox_etc_", suffix=".txt")
        os.write(_fd, _content.encode())
        os.close(_fd)
        _etc_tmp.append(_path)
        argv += ["--ro-bind", _path, _target]
    # read-write: workspace, skills, external dirs bound at their VIRTUAL paths
    for real, target in binds:
        argv += ["--bind", real, target]
    # HOME/TMPDIR -> tmpfs; PATH -> venv bin + system dirs. The subprocess env
    # is sanitized separately so no server secrets reach the sandboxed command.
    sandbox_path = _sandbox_path(venv)
    argv += ["--setenv", "HOME", "/tmp"]
    argv += ["--setenv", "TMPDIR", "/tmp"]
    argv += ["--setenv", "PATH", sandbox_path]
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
            env=_sanitize_env(env, home="/tmp", path=sandbox_path),
            cwd=None,
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
    finally:
        for _path in _etc_tmp:
            try:
                os.unlink(_path)
            except OSError:
                pass

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


def run_landlocked(
    command: str,
    *,
    allowed_rw_dirs: list[str],
    cwd: str,
    env: dict[str, str] | None,
    timeout: int,
    max_output_bytes: int,
) -> ExecuteResponse:
    """Run *command* confined by Linux Landlock (unprivileged FS isolation).

    Used when ``bwrap`` is unavailable. The command runs in a fresh helper
    process that applies a Landlock ruleset restricting filesystem access to:

    * the workspace / skills / external dirs (read-write),
    * system dirs + venv + ``/dev`` (read-only),
    * a handful of safe device files (``/dev/null`` ... read-write).

    Everything else on the host - including other users' workspace dirs and the
    application database - is invisible / denied. If Landlock cannot be applied
    the helper exits 127 without running the command (fail-closed).
    """
    import sys as _sys
    from pathlib import Path as _Path

    try:
        import easy_agent.landlock as _ll
    except Exception as exc:  # noqa: BLE001
        return ExecuteResponse(
            output=f"Error: Landlock 模块不可用，已拒绝执行命令: {exc}",
            exit_code=127,
            truncated=False,
        )
    if not _ll.landlock_usable():
        return ExecuteResponse(
            output=(
                "Error: 当前环境不支持 bwrap 与 Landlock，无法安全执行 shell 命令，已拒绝执行。"
                "请在容器启动时添加 --cap-add SYS_ADMIN --security-opt apparmor=unconfined，"
                "以启用 bwrap 沙箱；或在配置中设置 agent.sandbox_enabled=false（仅限可信单用户环境）。"
            ),
            exit_code=127,
            truncated=False,
        )

    ro_dirs = list(_SANDBOX_RO_DIRS)
    if "/dev" not in ro_dirs:
        ro_dirs.append("/dev")
    # NOTE: the venv is intentionally NOT added to ro_dirs. Its real host path
    # (typically /home/<user>/.../.venv) would expose /home; the confined
    # command uses the system Python instead. Landlock cannot remap paths, so
    # the workspace stays at its real path (confined to it).

    # Sanitize the environment so server secrets (JWT key, API keys, DB creds)
    # are never forwarded to the confined command, then add Landlock params.
    full_env = _sanitize_env(env, home=cwd, path=_sandbox_path(None))
    full_env["EASY_LANDLOCK_RW"] = os.pathsep.join(
        d for d in allowed_rw_dirs if d
    )
    full_env["EASY_LANDLOCK_RO"] = os.pathsep.join(d for d in ro_dirs if d)
    full_env["EASY_LANDLOCK_RW_FILES"] = os.pathsep.join(_ll._SAFE_DEV_FILES)
    full_env["EASY_LANDLOCK_CWD"] = cwd
    full_env["TMPDIR"] = cwd

    script = _Path(_ll.__file__)
    argv: list[str] = [
        _sys.executable, str(script), "sh", "-c", command,
    ]
    try:
        result = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            check=False,
            shell=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            env=full_env,
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
            output="Error: Python interpreter not found for Landlock executor.",
            exit_code=127,
            truncated=False,
        )

    # The helper exits 127 (with a stderr message) when confinement setup fails.
    if result.returncode == 127 and not result.stdout and result.stderr.strip():
        return ExecuteResponse(
            output=result.stderr.strip(),
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
