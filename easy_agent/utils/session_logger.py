"""Per-session conversation logging - append-only JSONL.

历史实现每记录一条（每条工具调用都会记）都会「读入整个 JSON 文件 -> append ->
全量 indent=2 重写」，长会话写盘 O(n²)（实测 120 条 -> 798KB、累计写盘约 48MB），
且同步文件 IO 直接压在事件循环上。

现在改为 append-only JSONL：每条 entry 只追加一行（O(1)），首次创建时写一行
session_meta。旧格式 ``{session_id}.json``（若存在）会在首次初始化时迁移为
JSONL，旧文件保留不动。读取方只需逐行 ``json.loads``。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _resolve_session_log_dir(username: str = "", log_date: Optional[str] = None) -> Path:
    """解析会话日志目录：{sessions_dir}/{username}/{YYYY-MM-DD}/

    使用配置项 ``sessions_dir`` 并按用户隔离子目录，再按日志日期（精确到天）
    分子目录；与 workspace/memories 的用户隔离约定一致。配置不可用时回退到
    ``logs/sessions`` 保持兼容。
    """
    try:
        from easy_agent.bootstrap.config import Config

        base = Config.get_user_sessions_dir(username)
        if log_date:
            base = base / log_date
        base.mkdir(parents=True, exist_ok=True)
        return base
    except Exception:
        fallback = Path("logs") / "sessions"
        if log_date:
            fallback = fallback / log_date
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class SessionLogger:
    """Logs full conversation as append-only JSONL per session.

    文件位置：``{sessions_dir}/{username}/{YYYY-MM-DD}/{session_id}.jsonl``。
    第一行为 ``session_meta``，其后每条日志一行；追加写，不重写历史。
    """

    def __init__(
        self,
        session_id: str,
        username: str = "",
        workspace: str = "",
        system_prompt: str = "",
    ):
        self.session_id = session_id
        log_date = datetime.now().strftime("%Y-%m-%d")
        log_dir = _resolve_session_log_dir(username, log_date)
        self.log_file = log_dir / f"{session_id}.jsonl"

        workspace = workspace or self._resolve_workspace(username)
        system_prompt = system_prompt or self._resolve_system_prompt()
        skills_dir = str(Path(workspace) / "skills") if workspace else ""
        memories_dir = self._resolve_memories_dir(username)

        self._meta: dict[str, Any] = {
            "type": "session_meta",
            "session_id": session_id,
            "username": username,
            "workspace": workspace,
            "skills_dir": skills_dir,
            "memories_dir": memories_dir,
            "system_prompt": system_prompt,
            "updated_at": datetime.now().isoformat(),
        }
        self._migrate_legacy_json(log_dir)
        if not self.log_file.exists() or self.log_file.stat().st_size == 0:
            self._append(self._meta)
        else:
            # 元信息可能随工作区/提示词变化，追加一行最新 meta（读取方取最后一条）。
            self._append(self._meta)

    # -- 兼容旧 JSON 文件：一次性迁移为 JSONL（旧文件保留） -----------------
    def _migrate_legacy_json(self, log_dir: Path) -> None:
        if self.log_file.exists():
            return
        legacy = log_dir / f"{self.session_id}.json"
        if not legacy.exists():
            return
        try:
            with open(legacy, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            with open(self.log_file, "a", encoding="utf-8") as out:
                meta = {k: v for k, v in data.items() if k != "entries"}
                meta["type"] = "session_meta"
                out.write(json.dumps(meta, ensure_ascii=False) + "\n")
                for entry in data.get("entries") or []:
                    if isinstance(entry, dict):
                        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info(f"会话日志已迁移为 JSONL: {legacy} -> {self.log_file}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"迁移旧会话日志失败（忽略，继续新建 JSONL）: {legacy} | {exc}")

    def _append(self, entry: dict) -> None:
        entry.setdefault("timestamp", datetime.now().isoformat())
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as exc:
            logger.warning(f"写入会话日志失败（忽略，不影响对话）: {self.log_file} | {exc}")

    @staticmethod
    def _resolve_workspace(username: str) -> str:
        """推导用户工作目录：{workspace_dir}/{username}/"""
        try:
            from easy_agent.bootstrap.config import Config

            return str(Config.get_user_workspace_dir(username))
        except Exception:
            return ""

    @staticmethod
    def _resolve_system_prompt() -> str:
        """从运行期 agent 配置中读取 system_prompt"""
        try:
            from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

            _cfg = get_runtime_agent_config()
            if _cfg:
                return _cfg.get("system_prompt", "") or ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _resolve_memories_dir(username: str) -> str:
        """推导用户长期记忆目录：{memories_dir}/{username}/"""
        try:
            from easy_agent.bootstrap.config import Config

            return str(Config.get_user_memories_dir(username))
        except Exception:
            return ""

    def log_user_message(
        self, message: str, files: Optional[list] = None, message_id: str = ""
    ):
        entry = {
            "type": "user_message",
            "message_id": message_id,
            "content": message,
        }
        if files:
            entry["files"] = [
                {
                    "filename": f.get("filename", ""),
                    "type": f.get("type", ""),
                    "size": f.get("size", 0),
                    "file_path": f.get("file_path", ""),
                }
                for f in files
            ]
        self._append(entry)

    def log_assistant_response(
        self,
        content: str,
        thinking: Optional[str] = None,
        thinking_duration: Optional[float] = None,
        tool_calls: Optional[list] = None,
        message_id: str = "",
    ):
        entry: dict[str, Any] = {
            "type": "assistant_response",
            "message_id": message_id,
            "content": content,
        }
        if thinking:
            entry["thinking"] = thinking
            entry["thinking_duration"] = thinking_duration
        if tool_calls:
            entry["tool_calls"] = [
                {
                    "tool_name": tc.get("tool_name", ""),
                    "tool_call_id": tc.get("tool_call_id", ""),
                    "arguments": tc.get("arguments", {}),
                    "result": tc.get("result", ""),
                    "success": tc.get("success", True),
                    "duration": tc.get("duration"),
                    "step": tc.get("step", 0),
                }
                for tc in tool_calls
            ]
        self._append(entry)

    def log_tool_call(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict,
        result: str = "",
        success: bool = True,
        duration: Optional[float] = None,
        step: int = 0,
        message_id: str = "",
    ):
        entry = {
            "type": "tool_call",
            "message_id": message_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "result": result,
            "success": success,
            "step": step,
        }
        if duration is not None:
            entry["duration"] = duration
        self._append(entry)
