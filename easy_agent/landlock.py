"""Landlock-based filesystem confinement for agent shell commands.

Landlock (Linux >= 5.13) lets an **unprivileged** process restrict its own
filesystem access to a set of allowed directories.  This is used as the
sandbox fallback when ``bwrap`` is unavailable (e.g. Docker without
``--cap-add SYS_ADMIN``): the ``bubblewrap`` container gives full isolation,
while Landlock confines the executed command to the workspace + system
directories without needing elevated privileges.

This module is deliberately self-contained (no ``easy_agent`` imports) so it
can also be executed as a standalone helper::

    python -m easy_agent.landlock sh -c '<command>'

reading ``EASY_LANDLOCK_RW`` / ``EASY_LANDLOCK_RO`` (colon-separated real
directories) from the environment, applying the restriction, then ``exec``'ing
the command.  Running confinement in a fresh helper process (rather than
``preexec_fn``) avoids fork-after-threads deadlocks in the async server.

If Landlock cannot be applied (unsupported kernel, denied by seccomp, or a
setup error) the helper exits ``127`` without executing the command - i.e.
**fail-closed**.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Landlock syscall numbers are identical on x86_64, arm64 and x86 (32-bit).
# On exotic architectures they may differ; the version probe below returns
# False there, which causes callers to fall back to fail-closed.
_SYSCALL_CREATE_RULESET = 444
_SYSCALL_ADD_RULE = 445
_SYSCALL_RESTRICT_SELF = 446

# landlock_ruleset_attr.handled_access_fs bitmask (Landlock ABI v1).
_ACCESS_FS_EXECUTE = 1 << 0
_ACCESS_FS_WRITE_FILE = 1 << 1
_ACCESS_FS_READ_FILE = 1 << 2
_ACCESS_FS_READ_DIR = 1 << 3
_ACCESS_FS_REMOVE_DIR = 1 << 4
_ACCESS_FS_REMOVE_FILE = 1 << 5
_ACCESS_FS_MAKE_CHAR = 1 << 6
_ACCESS_FS_MAKE_DIR = 1 << 7
_ACCESS_FS_MAKE_REG = 1 << 8
_ACCESS_FS_MAKE_SOCK = 1 << 9
_ACCESS_FS_MAKE_FIFO = 1 << 10
_ACCESS_FS_MAKE_BLOCK = 1 << 11
_ACCESS_FS_MAKE_SYM = 1 << 12

# Every v1 filesystem access: anything not explicitly allowed by a rule is
# denied.  This is the set of operations we "handle" (restrict).
_ALL_ACCESS_FS = (
    _ACCESS_FS_EXECUTE
    | _ACCESS_FS_WRITE_FILE
    | _ACCESS_FS_READ_FILE
    | _ACCESS_FS_READ_DIR
    | _ACCESS_FS_REMOVE_DIR
    | _ACCESS_FS_REMOVE_FILE
    | _ACCESS_FS_MAKE_CHAR
    | _ACCESS_FS_MAKE_DIR
    | _ACCESS_FS_MAKE_REG
    | _ACCESS_FS_MAKE_SOCK
    | _ACCESS_FS_MAKE_FIFO
    | _ACCESS_FS_MAKE_BLOCK
    | _ACCESS_FS_MAKE_SYM
)

# Read-only access: may execute binaries, read files and list directories,
# but not create / write / delete anything.
_RO_ACCESS_FS = _ACCESS_FS_EXECUTE | _ACCESS_FS_READ_FILE | _ACCESS_FS_READ_DIR

# File-only read/write/execute access. Landlock rejects directory-only flags
# (MAKE_*, REMOVE_*, READ_DIR) on a rule whose path is a regular file, so device
# files such as ``/dev/null`` must use this reduced mask.
_FILE_RW_ACCESS_FS = _ACCESS_FS_EXECUTE | _ACCESS_FS_READ_FILE | _ACCESS_FS_WRITE_FILE

# Device files that shell redirects commonly write to (``> /dev/null``). They
# are allow-listed individually with read/write access while the rest of ``/dev``
# stays read-only, so block devices such as ``/dev/sda`` remain protected.
_SAFE_DEV_FILES = (
    "/dev/null",
    "/dev/zero",
    "/dev/full",
    "/dev/random",
    "/dev/urandom",
)

_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_libc = ctypes.CDLL(None, use_errno=True)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


# The kernel struct landlock_path_beneath_attr is __attribute__((packed)):
# { u64 allowed_access; s32 parent_fd; } -> 12 bytes, no padding.
class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


_landlock_usable: bool | None = None


def landlock_usable() -> bool:
    """Return ``True`` if the Landlock ABI is available to this process.

    Probes ``landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)``
    which returns the highest supported ABI version (>= 1) or -1 (errno set)
    when unsupported / blocked by seccomp.  Result is cached.
    """
    global _landlock_usable
    if _landlock_usable is not None:
        return _landlock_usable
    if sys.platform != "linux" and not sys.platform.startswith("linux"):
        _landlock_usable = False
        return False
    try:
        version = _libc.syscall(
            _SYSCALL_CREATE_RULESET, None, ctypes.c_size_t(0), _LANDLOCK_CREATE_RULESET_VERSION
        )
        if version < 0:
            errno = ctypes.get_errno()
            logger.info(
                "[landlock] 不可用 (landlock_create_ruleset 返回 %d, errno=%d)", version, errno
            )
            _landlock_usable = False
        else:
            _landlock_usable = version >= 1
            logger.info("[landlock] 可用，ABI 版本=%d", version)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[landlock] 探测异常: %s", exc)
        _landlock_usable = False
    return _landlock_usable


def _add_rule(ruleset_fd: int, path: str, access: int) -> None:
    """Add a path-beneath rule permitting ``access`` beneath ``path``."""
    parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        attr = _PathBeneathAttr(allowed_access=access, parent_fd=parent_fd)
        rc = _libc.syscall(
            _SYSCALL_ADD_RULE,
            ctypes.c_int(ruleset_fd),
            _LANDLOCK_RULE_PATH_BENEATH,
            ctypes.byref(attr),
            ctypes.c_uint(0),
        )
        if rc < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"landlock_add_rule({path}) failed (errno={errno})")
    finally:
        os.close(parent_fd)


def apply_landlock(rw_dirs: list[str], ro_dirs: list[str], rw_files: list[str] | None = None) -> None:
    """Restrict the calling thread (and all future children) to the given paths.

    * ``rw_dirs`` - full read/write/create/delete access beneath each directory.
    * ``ro_dirs`` - read/execute/list only (directories).
    * ``rw_files`` - read/write/execute on individual files (e.g. ``/dev/null``);
      Landlock rejects directory-only flags on regular files, so device files
      are allow-listed here with a reduced mask while ``/dev`` stays read-only.

    Must be called before ``exec``'ing the confined command.  Raises on any
    setup failure so the caller can fail-closed instead of running unconfined.
    """
    attr = _RulesetAttr(handled_access_fs=_ALL_ACCESS_FS)
    ruleset_fd = _libc.syscall(
        _SYSCALL_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), ctypes.c_uint(0)
    )
    if ruleset_fd < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"landlock_create_ruleset failed (errno={errno})")
    try:
        for path in rw_dirs:
            if path and os.path.isdir(path):
                _add_rule(ruleset_fd, path, _ALL_ACCESS_FS)
        for path in ro_dirs:
            if path and os.path.isdir(path):
                _add_rule(ruleset_fd, path, _RO_ACCESS_FS)
        for path in (rw_files or []):
            if path and os.path.exists(path) and not os.path.isdir(path):
                _add_rule(ruleset_fd, path, _FILE_RW_ACCESS_FS)
        # PR_SET_NO_NEW_PRIVS is mandatory before landlock_restrict_self: it
        # prevents the confined process from regaining privileges via setuid.
        if _libc.prctl(_PR_SET_NO_NEW_PRIVS, ctypes.c_ulong(1), 0, 0, 0) < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"prctl(PR_SET_NO_NEW_PRIVS) failed (errno={errno})")
        rc = _libc.syscall(_SYSCALL_RESTRICT_SELF, ctypes.c_int(ruleset_fd), ctypes.c_uint(0))
        if rc < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"landlock_restrict_self failed (errno={errno})")
    finally:
        os.close(ruleset_fd)


def _main() -> int:
    """Standalone executor: apply Landlock from env, then exec the command."""
    sep = os.pathsep
    rw = [d for d in os.environ.get("EASY_LANDLOCK_RW", "").split(sep) if d]
    ro = [d for d in os.environ.get("EASY_LANDLOCK_RO", "").split(sep) if d]
    rf = [d for d in os.environ.get("EASY_LANDLOCK_RW_FILES", "").split(sep) if d]
    cwd = os.environ.get("EASY_LANDLOCK_CWD")
    try:
        apply_landlock(rw, ro, rw_files=rf)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[landlock] 隔离设置失败，拒绝执行命令: {exc}\n")
        return 127
    # Strip Landlock control variables so the confined command cannot read them
    # (they contain real host paths) via `printenv` / `/proc/self/environ`.
    for k in [k for k in os.environ if k.startswith("EASY_LANDLOCK_")]:
        os.environ.pop(k, None)
    argv = sys.argv[1:]
    if not argv:
        return 0
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            pass
    try:
        os.execvp(argv[0], argv)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[landlock] exec 失败: {exc}\n")
        return 127
    return 127  # unreachable


if __name__ == "__main__":
    sys.exit(_main())
