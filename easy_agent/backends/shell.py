"""Sandboxed LocalShellBackend with virtual-path translation."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse

from easy_agent.agent.hitl import _is_directory_deletion
from easy_agent.sandboxing.landlock import landlock_usable
from easy_agent.sandboxing.sandbox import bwrap_usable, run_landlocked, run_sandboxed

logger = logging.getLogger(__name__)
_IS_WINDOWS = sys.platform == "win32"
_warned_landlock_network = False


class PathTranslatingShell(LocalShellBackend):
    """LocalShellBackend 包装：execute 命令中自动将虚拟路径翻译为实际路径。

    ``sandbox_network`` / ``readonly_binds`` / ``readonly_dirs``（DA-07）：沙箱网络
    （``host`` / ``none``），以及要只读挂载的路径——bwrap 用 ``(实际路径, 虚拟路径)``
    覆盖在可写挂载之上；Landlock 不能在可写目录里再收紧，``readonly_dirs`` 整体改为只读。
    """

    def __init__(
        self,
        path_mappings: dict[str, str],
        *args,
        sandbox_enabled: bool = True,
        sandbox_network: str = "host",
        readonly_binds: list[tuple[str, str]] | None = None,
        readonly_dirs: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._sandbox_network = sandbox_network
        self._readonly_binds = list(readonly_binds or [])
        self._readonly_dirs = [str(Path(d).resolve()) for d in readonly_dirs or []]
        self._sandbox_id = getattr(self, "_sandbox_id", "sandbox")
        self._is_windows = _IS_WINDOWS
        self._rules = [
            (
                re.compile(
                    rf'(^|[\s"\'&|;(=])({re.escape(v.rstrip("/"))})(?=/|$|[\s"\'&|;),=])'
                ),
                real.rstrip("/\\"),
            )
            for v, real in sorted(path_mappings.items(), key=lambda x: -len(x[0]))
        ]
        self._reverse_rules = [
            (real.rstrip("/\\"), v.rstrip("/"))
            for v, real in sorted(path_mappings.items(), key=lambda x: -len(x[1]))
        ]
        self._sandbox_enabled = sandbox_enabled
        self._allowed_rw_dirs = self._compute_allowed_dirs(path_mappings)
        self._use_bwrap = sandbox_enabled and bwrap_usable()
        self._use_landlock = (
            sandbox_enabled and not self._use_bwrap and landlock_usable()
        )
        if self._use_bwrap:
            self._bind_pairs = [
                (real.rstrip("/"), virtual.rstrip("/"))
                for virtual, real in path_mappings.items()
            ]
            self._virtual_cwd = next(
                iter(path_mappings.keys()), str(self.cwd)
            ).rstrip("/")
            self._rules = []
            self._reverse_rules = []
        else:
            self._bind_pairs = None
            self._virtual_cwd = None
        if sandbox_enabled:
            if self._use_bwrap:
                mode = "bwrap 沙箱隔离"
            elif self._use_landlock:
                mode = "Landlock 隔离（bwrap 不可用，已降级为非特权文件系统隔离）"
            else:
                mode = "fail-closed（bwrap 与 Landlock 均不可用，shell 命令将被拒绝）"
            logger.info(f"[{self._sandbox_id}] shell 沙箱已启用: {mode}")

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        translated = command
        for pat, real in self._rules:
            translated = pat.sub(lambda m: m.group(1) + real, translated)
        if self._is_windows:
            for _pattern, real in self._rules:
                prefix = real.rstrip("/\\")
                translated = translated.replace(f"{prefix}/", f"{prefix}\\")
        if translated != command:
            logger.debug(
                "execute path translation: %s → %s", command[:80], translated[:80]
            )
        if _is_directory_deletion(translated):
            logger.warning(f"[execute] 拒绝目录删除命令: {translated[:120]}")
            return ExecuteResponse(
                output="Error: 不允许删除目录/文件夹。项目根目录和 workspace 下的文件夹受保护，"
                "无法通过 rmdir、rm -r 等命令删除。如需删除文件，请使用 rm（不带 -r/-R/-d 参数）。",
                exit_code=1,
                truncated=False,
            )
        result = self._run_sandboxed(translated, timeout=timeout)
        if self._reverse_rules and result.output:
            output = result.output
            for real, v in self._reverse_rules:
                if self._is_windows:
                    candidates = (
                        real,
                        real.replace("/", "\\"),
                        real.replace("\\", "/"),
                    )
                    for candidate in candidates:
                        output = re.sub(
                            re.escape(candidate),
                            v.rstrip("/"),
                            output,
                            flags=re.IGNORECASE,
                        )
                elif real in output:
                    output = output.replace(real, v)
            if output != result.output:
                result = ExecuteResponse(
                    output=output,
                    exit_code=result.exit_code,
                    truncated=result.truncated,
                )
        return result

    def _warn_if_network_unenforced(self) -> None:
        global _warned_landlock_network
        if self._sandbox_network == "none" and not _warned_landlock_network:
            _warned_landlock_network = True
            logger.warning(
                "sandbox_network=none 未生效：bwrap 不可用，Landlock 降级模式受内核限制无法断网"
            )

    def _compute_allowed_dirs(self, path_mappings: dict[str, str]) -> list[str]:
        dirs: set[str] = set()
        try:
            dirs.add(str(Path(self.cwd).resolve()))
        except Exception:
            dirs.add(str(self.cwd))
        for real in path_mappings.values():
            try:
                dirs.add(str(Path(real).resolve()))
            except Exception:
                dirs.add(str(Path(real)))
        dirs -= set(self._readonly_dirs)
        return sorted(dirs, key=len, reverse=True)

    def _run_sandboxed(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if not self._sandbox_enabled:
            return super().execute(command, timeout=timeout)
        if self._use_bwrap:
            return run_sandboxed(
                command,
                binds=self._bind_pairs,
                cwd=self._virtual_cwd,
                env=self._env,
                timeout=timeout if timeout is not None else self._default_timeout,
                max_output_bytes=self._max_output_bytes,
                network=self._sandbox_network,
                ro_binds=self._readonly_binds,
            )
        if self._use_landlock:
            self._warn_if_network_unenforced()
            return run_landlocked(
                command,
                allowed_rw_dirs=self._allowed_rw_dirs,
                cwd=str(self.cwd),
                env=self._env,
                timeout=timeout if timeout is not None else self._default_timeout,
                max_output_bytes=self._max_output_bytes,
                extra_ro_dirs=self._readonly_dirs,
            )
        logger.warning(
            f"[{self._sandbox_id}] 拒绝执行 shell 命令（无可用沙箱隔离）: {command[:120]}"
        )
        unsupported_message = (
            "Error: Windows 当前未提供 bwrap/Landlock 等效沙箱，已拒绝执行 shell 命令。\n"
            "如仅在可信单用户环境运行，可在配置中设置 agent.sandbox_enabled=false。"
            if self._is_windows
            else (
                "Error: 当前环境不支持 bwrap 与 Landlock，无法安全执行 shell 命令，已拒绝执行。\n"
                "请在容器启动时添加 --security-opt seccomp=unconfined --security-opt apparmor=unconfined "
                "--security-opt systempaths=unconfined 以启用 bwrap 沙箱。"
            )
        )
        return ExecuteResponse(
            output=unsupported_message,
            exit_code=127,
            truncated=False,
        )


_PathTranslatingShell = PathTranslatingShell
