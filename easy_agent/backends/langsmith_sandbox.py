"""Opt-in LangSmith Sandbox remote execution backend (experimental).

启用方式::

    EASY_SANDBOX=langsmith
    LANGSMITH_API_KEY=...
    # 可选
    LANGSMITH_ENDPOINT=...
    EASY_LANGSMITH_SNAPSHOT=...
    EASY_SANDBOX_REMOTE_ROOT=/workspace
    EASY_SANDBOX_SYNC_MAX_MB=20

只有 ``execute`` 被转发到远端 sandbox；``read_file`` / ``write_file`` /
``glob`` / ``grep`` 等文件工具仍然走本地 workspace（hybrid）。每次 execute
前后做 best-effort 同步：本地 -> 远端（执行前），远端 -> 本地（执行后回写）。
默认 ``EASY_SANDBOX=local``，本模块不会被导入路径之外的行为触发。
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import shlex
import threading
from pathlib import Path
from typing import Any, Callable, Iterable

from deepagents.backends.protocol import ExecuteResponse

from easy_agent.backends.shell import _PathTranslatingShell
from easy_agent.bootstrap.env import env_str
from easy_agent.sandboxing.sandbox import host_safe_env

logger = logging.getLogger(__name__)

DEFAULT_REMOTE_ROOT = "/workspace"
DEFAULT_SYNC_MAX_MB = 20.0
#: 这些目录（任意层级）不上传，避免把依赖/缓存目录同步到远端。
SYNC_EXCLUDE_DIR_NAMES = frozenset({".deps", ".venv", "node_modules", "__pycache__"})

_DOWNLOAD_BATCH_SIZE = 100
_MKDIR_BATCH_SIZE = 100


def sandbox_mode() -> str:
    """读取 ``EASY_SANDBOX``：``local``（默认）或 ``langsmith``。

    未知取值直接抛 ValueError（fail-closed），避免拼写错误时静默退回本地
    执行而让调用方误以为命令跑在远端 sandbox 里。
    """
    raw = (env_str("EASY_SANDBOX") or "").strip().lower()
    if raw in ("", "local"):
        return "local"
    if raw == "langsmith":
        return "langsmith"
    raise ValueError(
        "EASY_SANDBOX 配置无效："
        f"{raw!r}；仅支持 'local'（默认）或 'langsmith'（实验性远端 sandbox）。"
    )


def _sync_max_mb_from_env() -> float:
    raw = (env_str("EASY_SANDBOX_SYNC_MAX_MB") or "").strip()
    if not raw:
        return DEFAULT_SYNC_MAX_MB
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"EASY_SANDBOX_SYNC_MAX_MB 必须是正数，当前为 {raw!r}"
        ) from exc
    if value <= 0:
        raise ValueError(f"EASY_SANDBOX_SYNC_MAX_MB 必须大于 0，当前为 {value}")
    return value


def _normalize_remote_root(root: str) -> str:
    value = str(root or "").strip()
    if not value.startswith("/"):
        raise ValueError(f"EASY_SANDBOX_REMOTE_ROOT 必须是绝对路径，当前为 {value!r}")
    normalized = posixpath.normpath(value)
    if normalized in ("", "/", ".") or normalized.startswith("../"):
        raise ValueError(f"EASY_SANDBOX_REMOTE_ROOT 不能是根目录或包含 ..，当前为 {value!r}")
    return normalized


def _normalize_virtual_path(path: str) -> str:
    value = str(path or DEFAULT_REMOTE_ROOT).strip()
    if not value.startswith("/"):
        value = "/" + value
    normalized = posixpath.normpath(value)
    return normalized if normalized not in ("", ".", "/") else DEFAULT_REMOTE_ROOT


def _build_path_rules(
    sources: Iterable[str], target: str
) -> list[tuple[re.Pattern[str], str]]:
    """构造「路径前缀 -> 目标前缀」替换规则（按前缀长度降序）。"""
    normalized_target = target.rstrip("/")
    unique: set[str] = set()
    for source in sources:
        candidate = str(source or "").rstrip("/")
        if not candidate or candidate == normalized_target:
            continue
        unique.add(candidate)
    rules: list[tuple[re.Pattern[str], str]] = []
    for candidate in sorted(unique, key=len, reverse=True):
        # 只在路径起点或分隔符后匹配，避免 /workspace 误伤 /workspace2。
        pattern = re.compile(
            rf'(^|[\s"\'&|;(=]){re.escape(candidate)}(?=/|\\|$|[\s"\'&|;),=])'
        )
        rules.append((pattern, normalized_target))
    return rules


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def create_langsmith_backend(
    workspace_dir: str | Path,
    path_mappings: dict[str, str],
    *,
    workspace_virtual_path: str = DEFAULT_REMOTE_ROOT,
    remote_root: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    sandbox_factory: Callable[[], Any] | None = None,
    snapshot_name: str | None = None,
    max_sync_mb: float | None = None,
    sid: str = "",
) -> "LangSmithExecuteShell":
    """创建 hybrid execute backend：文件走本地，命令走 LangSmith Sandbox。

    Args:
        workspace_dir: 本地 workspace 根目录（同步源/回写目标）。
        path_mappings: 传给 ``_PathTranslatingShell`` 的虚拟路径 -> 本地路径映射。
        workspace_virtual_path: workspace 在虚拟文件系统中的前缀（通常 ``/workspace``）。
        remote_root: 远端 sandbox 内的 workspace 根目录；默认读
            ``EASY_SANDBOX_REMOTE_ROOT``，再退回 ``/workspace``。
        timeout: 默认命令超时（秒），与本地 backend 默认值保持一致。
        env: 追加给本地文件 backend 的环境变量（远端命令环境由 sandbox 决定）。
        sandbox_factory: 测试注入点；零参可调用对象，返回一个 fake/真实
            ``LangSmithSandbox`` backend。注入时不再检查 LangSmith 凭据。
        snapshot_name: snapshot 名称；默认读 ``EASY_LANGSMITH_SNAPSHOT``。
        max_sync_mb: 单文件同步上限（MB）；默认读
            ``EASY_SANDBOX_SYNC_MAX_MB``（默认 20）。
        sid: 日志标识。

    Raises:
        RuntimeError: 缺少 LangSmith 凭据、SDK 不可用或创建 sandbox 失败。
    """
    resolved_root = _normalize_remote_root(
        remote_root or env_str("EASY_SANDBOX_REMOTE_ROOT") or DEFAULT_REMOTE_ROOT
    )
    resolved_max_mb = (
        float(max_sync_mb) if max_sync_mb is not None else _sync_max_mb_from_env()
    )
    if resolved_max_mb <= 0:
        raise ValueError(f"max_sync_mb 必须大于 0，当前为 {resolved_max_mb}")

    if sandbox_factory is not None:
        try:
            remote = sandbox_factory()
        except Exception as exc:  # noqa: BLE001 - 统一变成清晰的 RuntimeError
            raise RuntimeError(
                f"通过 sandbox_factory 创建 LangSmith Sandbox backend 失败：{exc}"
            ) from exc
        if remote is None:
            raise RuntimeError("sandbox_factory 返回了 None，无法启用远端 execute")
    else:
        api_key = (
            env_str("LANGSMITH_API_KEY") or env_str("LANGCHAIN_API_KEY") or ""
        ).strip()
        if not api_key:
            raise RuntimeError(
                "EASY_SANDBOX=langsmith 需要 LangSmith 凭据：请设置 "
                "LANGSMITH_API_KEY（或 LANGCHAIN_API_KEY），并确认 "
                "LANGSMITH_ENDPOINT 指向可用的 LangSmith 服务。"
            )
        try:
            from deepagents.backends import LangSmithSandbox
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "当前 deepagents 未提供 LangSmithSandbox；请安装 "
                "deepagents>=0.7.28 与 langsmith[sandbox]。"
            ) from exc
        try:
            from langsmith.sandbox import SandboxClient
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "未安装 langsmith.sandbox（langsmith[sandbox]），"
                "无法启用 EASY_SANDBOX=langsmith。"
            ) from exc

        raw_snapshot = (
            snapshot_name
            if snapshot_name is not None
            else env_str("EASY_LANGSMITH_SNAPSHOT")
        )
        resolved_snapshot = str(raw_snapshot or "").strip() or None
        try:
            client = SandboxClient()
            sandbox = client.create_sandbox(
                snapshot_name=resolved_snapshot or None,
                wait_for_ready=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "创建 LangSmith Sandbox 失败"
                f"（snapshot={resolved_snapshot or '<default>'}）：{exc}"
            ) from exc
        try:
            remote = LangSmithSandbox(sandbox)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"包装 LangSmith Sandbox 失败：{exc}") from exc

    logger.info(
        "[%s] EASY_SANDBOX=langsmith：execute 转发到远端 LangSmith Sandbox "
        "(root=%s)，文件工具仍使用本地 workspace（实验性）",
        sid or "sandbox",
        resolved_root,
    )
    return LangSmithExecuteShell(
        sandbox_backend=remote,
        workspace_dir=workspace_dir,
        path_mappings=path_mappings,
        workspace_virtual_path=workspace_virtual_path,
        remote_root=resolved_root,
        timeout=timeout,
        env=env,
        max_sync_mb=resolved_max_mb,
        sid=sid,
    )


class LangSmithExecuteShell(_PathTranslatingShell):
    """把 ``execute`` 转发到 LangSmith Sandbox 的 hybrid backend。

    文件操作继承 ``_PathTranslatingShell``（本地 workspace）；``execute``
    每次先上传本地 workspace，再远端执行，最后把远端文件回写本地。
    """

    def __init__(
        self,
        *,
        sandbox_backend: Any,
        workspace_dir: str | Path,
        path_mappings: dict[str, str],
        workspace_virtual_path: str = DEFAULT_REMOTE_ROOT,
        remote_root: str = DEFAULT_REMOTE_ROOT,
        timeout: int = 120,
        env: dict[str, str] | None = None,
        max_sync_mb: float | None = None,
        exclude_dir_names: Iterable[str] | None = None,
        sid: str = "",
    ) -> None:
        if sandbox_backend is None:
            raise ValueError("sandbox_backend 不能为空")
        self._workspace_root = Path(workspace_dir).expanduser().resolve()
        self._workspace_virtual_path = _normalize_virtual_path(workspace_virtual_path)
        self._remote_root = _normalize_remote_root(remote_root)
        self._remote = sandbox_backend
        resolved_max_mb = (
            _sync_max_mb_from_env() if max_sync_mb is None else float(max_sync_mb)
        )
        self._max_sync_bytes = int(resolved_max_mb * 1024 * 1024)
        if self._max_sync_bytes <= 0:
            raise ValueError(f"max_sync_mb 必须大于 0，当前为 {resolved_max_mb}")
        self._exclude_dir_names = frozenset(
            exclude_dir_names or SYNC_EXCLUDE_DIR_NAMES
        )
        self._sid = sid or "sandbox"
        self._sync_lock = threading.Lock()
        self._synced_once = False
        self._remote_dirs: set[str] = set()
        self._command_rules = _build_path_rules(
            (
                self._workspace_virtual_path,
                self._workspace_root.as_posix(),
                str(self._workspace_root),
            ),
            self._remote_root,
        )
        self._output_rules = _build_path_rules(
            (self._remote_root,), self._workspace_virtual_path
        )

        # 文件读写/路径翻译继续由父类提供；sandbox_enabled=False 表示不启用
        # bwrap/Landlock —— 命令不会在本地执行，而是走远端 sandbox。
        super().__init__(
            path_mappings=path_mappings,
            root_dir=str(self._workspace_root),
            virtual_mode=True,
            # 本地 env 不用于执行（命令都发往远端），但也不在对象上留一份宿主密钥。
            inherit_env=False,
            env={**host_safe_env(), **(env or {})},
            timeout=timeout,
            sandbox_enabled=False,
        )

    # ── 同步 ──────────────────────────────────────────────────
    def _collect_local_files(self) -> list[tuple[str, str, bytes]]:
        """收集待上传文件：``(相对路径, 远端路径, 内容)``。"""
        if not self._workspace_root.is_dir():
            logger.warning(
                "[%s] 本地 workspace 不存在，跳过上传：%s",
                self._sid,
                self._workspace_root,
            )
            return []

        files: list[tuple[str, str, bytes]] = []
        skipped_large = 0
        skipped_unreadable = 0
        for current, dirnames, filenames in os.walk(
            self._workspace_root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in self._exclude_dir_names
                and not (current_path / name).is_symlink()
            )
            for name in sorted(filenames):
                path = current_path / name
                if path.is_symlink():
                    continue
                try:
                    size = path.stat().st_size
                except OSError as exc:
                    skipped_unreadable += 1
                    logger.warning("[%s] 跳过无法 stat 的文件 %s: %s", self._sid, path, exc)
                    continue
                if size > self._max_sync_bytes:
                    skipped_large += 1
                    continue
                try:
                    content = path.read_bytes()
                except OSError as exc:
                    skipped_unreadable += 1
                    logger.warning("[%s] 跳过无法读取的文件 %s: %s", self._sid, path, exc)
                    continue
                relative = path.relative_to(self._workspace_root).as_posix()
                remote_path = posixpath.join(self._remote_root, relative)
                files.append((relative, remote_path, content))

        if skipped_large:
            logger.warning(
                "[%s] 跳过 %d 个超过 %.1f MB 的文件（EASY_SANDBOX_SYNC_MAX_MB）",
                self._sid,
                skipped_large,
                self._max_sync_bytes / (1024 * 1024),
            )
        if skipped_unreadable:
            logger.warning(
                "[%s] 跳过 %d 个无法读取的文件", self._sid, skipped_unreadable
            )
        return files

    def _ensure_remote_dirs(self, directories: Iterable[str]) -> None:
        pending = sorted(
            {directory for directory in directories if directory}
            - self._remote_dirs
        )
        if not pending:
            return
        for chunk in _chunks(pending, _MKDIR_BATCH_SIZE):
            command = "mkdir -p " + " ".join(shlex.quote(item) for item in chunk)
            response = self._remote.execute(command, timeout=self._default_timeout)
            exit_code = getattr(response, "exit_code", 0)
            if exit_code != 0:
                output = getattr(response, "output", "") or ""
                raise RuntimeError(
                    f"远端创建目录失败（{command}，exit={exit_code}）：{output[:500]}"
                )
        self._remote_dirs.update(pending)

    def _sync_to_remote(self) -> None:
        files = self._collect_local_files()
        directories = {self._remote_root}
        for _, remote_path, _ in files:
            parent = posixpath.dirname(remote_path)
            if parent:
                directories.add(parent)
        self._ensure_remote_dirs(directories)

        if files:
            payload = [(remote_path, content) for _, remote_path, content in files]
            responses = self._remote.upload_files(payload)
            responses = responses or []
            errors: list[str] = []
            returned_paths = set()
            for response in responses:
                path = getattr(response, "path", "") or ""
                returned_paths.add(path)
                error = getattr(response, "error", None)
                if error:
                    errors.append(f"{path or '<unknown>'}: {error}")
            missing = [path for path, _ in payload if path not in returned_paths]
            if missing:
                errors.append(f"{len(missing)} 个文件没有上传响应，例如 {missing[0]}")
            if errors:
                raise RuntimeError("上传 workspace 到 LangSmith Sandbox 失败：" + "; ".join(errors[:5]))
        self._synced_once = True

    def _relative_remote_path(self, remote_path: str) -> str | None:
        root = self._remote_root.rstrip("/")
        if not remote_path.startswith(root + "/"):
            return None
        relative = remote_path[len(root) + 1 :]
        return relative or None

    def _write_downloaded_file(self, response: Any) -> None:
        path = getattr(response, "path", "") or ""
        error = getattr(response, "error", None)
        content = getattr(response, "content", None)
        if error or content is None:
            logger.warning(
                "[%s] 下载远端文件失败 %s: %s",
                self._sid,
                path or "<unknown>",
                error or "empty content",
            )
            return
        relative = self._relative_remote_path(path)
        if relative is None:
            logger.warning("[%s] 忽略 workspace 之外的远端文件：%s", self._sid, path)
            return
        local_path = (self._workspace_root / relative).resolve()
        try:
            local_path.relative_to(self._workspace_root)
        except ValueError:
            logger.warning("[%s] 拒绝越界回写路径：%s", self._sid, path)
            return
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(content)
        except OSError as exc:
            logger.warning(
                "[%s] 回写本地文件失败 %s: %s", self._sid, local_path, exc
            )

    def _sync_from_remote(self) -> None:
        try:
            result = self._remote.glob("**/*", path=self._remote_root)
        except Exception as exc:  # noqa: BLE001 - 回写是 best-effort
            logger.warning(
                "[%s] 远端 glob 失败，跳过回写：%s", self._sid, exc, exc_info=True
            )
            return
        error = getattr(result, "error", None)
        if error:
            logger.warning("[%s] 远端 glob 返回错误，跳过回写：%s", self._sid, error)
            return

        remote_files: list[str] = []
        for info in getattr(result, "matches", None) or []:
            if isinstance(info, dict):
                path = info.get("path")
                is_dir = bool(info.get("is_dir", False))
            else:
                path = getattr(info, "path", None)
                is_dir = bool(getattr(info, "is_dir", False))
            if not path or is_dir:
                continue
            if self._relative_remote_path(str(path)) is None:
                continue
            remote_files.append(str(path))
        if not remote_files:
            return

        for chunk in _chunks(remote_files, _DOWNLOAD_BATCH_SIZE):
            try:
                responses = self._remote.download_files(chunk)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[%s] 下载远端文件失败（批次 %d），跳过：%s",
                    self._sid,
                    len(chunk),
                    exc,
                    exc_info=True,
                )
                continue
            for response in responses or []:
                self._write_downloaded_file(response)

    # ── execute ────────────────────────────────────────────────
    def _translate_command(self, command: str) -> str:
        translated = command
        for pattern, replacement in self._command_rules:
            translated = pattern.sub(
                lambda match, target=replacement: match.group(1) + target,
                translated,
            )
        return translated

    def _wrap_command(self, command: str) -> str:
        translated = self._translate_command(command)
        return f"cd {shlex.quote(self._remote_root)} && {translated}"

    def _reverse_output(self, result: ExecuteResponse) -> ExecuteResponse:
        if not self._output_rules or not result.output:
            return result
        output = result.output
        for pattern, replacement in self._output_rules:
            output = pattern.sub(
                lambda match, target=replacement: match.group(1) + target,
                output,
            )
        if output == result.output:
            return result
        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )

    def execute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )
        if timeout is not None and timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout}")

        with self._sync_lock:
            try:
                self._sync_to_remote()
            except Exception as exc:  # noqa: BLE001
                if not self._synced_once:
                    logger.warning(
                        "[%s] 首次同步工作区失败，拒绝执行：%s",
                        self._sid,
                        exc,
                        exc_info=True,
                    )
                    return ExecuteResponse(
                        output=(
                            "Error: 同步 workspace 到 LangSmith Sandbox 失败，"
                            f"已拒绝执行（避免命令看不到输入文件）：{exc}"
                        ),
                        exit_code=1,
                        truncated=False,
                    )
                logger.warning(
                    "[%s] 同步工作区失败，继续执行（使用上次同步状态）：%s",
                    self._sid,
                    exc,
                    exc_info=True,
                )

            result = self._remote.execute(self._wrap_command(command), timeout=timeout)
            # 回写失败只记 warning，不影响远端执行结果。
            self._sync_from_remote()
            return self._reverse_output(result)

    async def aexecute(
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        """异步包装：同步协议在 worker 线程执行（含前后同步）。"""
        return await asyncio.to_thread(self.execute, command, timeout=timeout)


__all__ = [
    "DEFAULT_REMOTE_ROOT",
    "DEFAULT_SYNC_MAX_MB",
    "SYNC_EXCLUDE_DIR_NAMES",
    "LangSmithExecuteShell",
    "create_langsmith_backend",
    "sandbox_mode",
]