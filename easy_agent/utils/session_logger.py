"""Per-session conversation logging - stores complete JSON logs"""

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
        from easy_agent.config import Config

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
    """Logs full conversation as structured JSON per session.

    文件位置：``{sessions_dir}/{username}/{YYYY-MM-DD}/{session_id}.json``，
    按用户 + 日志日期（精确到天）分目录隔离。
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
        self.log_file = log_dir / f"{session_id}.json"

        # workspace / system_prompt 未显式传入时自动推导；并补充技能、记忆目录
        workspace = workspace or self._resolve_workspace(username)
        system_prompt = system_prompt or self._resolve_system_prompt()
        skills_dir = str(Path(workspace) / "skills") if workspace else ""
        memories_dir = self._resolve_memories_dir(username)

        # 多轮对话中每轮都会 new SessionLogger 实例：若文件已存在则加载已有内容
        # （保留历史 entries），仅用最新参数更新基础字段，避免覆盖只留下当前轮。
        loaded = self._load_existing()
        if loaded is not None:
            self._data = loaded
            if not isinstance(self._data.get("entries"), list):
                self._data["entries"] = []
            if workspace:
                self._data["workspace"] = workspace
            if system_prompt:
                self._data["system_prompt"] = system_prompt
            self._data["session_id"] = session_id
            self._data["username"] = username
            self._data["skills_dir"] = skills_dir
            self._data["memories_dir"] = memories_dir
        else:
            self._data: dict[str, Any] = {
                "session_id": session_id,
                "username": username,
                "workspace": workspace,
                "system_prompt": system_prompt,
                "skills_dir": skills_dir,
                "memories_dir": memories_dir,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "entries": [],
            }
        self._flush()

    @staticmethod
    def _resolve_workspace(username: str) -> str:
        """推导用户工作目录：{workspace_dir}/{username}/"""
        try:
            from easy_agent.config import Config

            return str(Config.get_user_workspace_dir(username))
        except Exception:
            return ""

    @staticmethod
    def _resolve_system_prompt() -> str:
        """从运行期 agent 配置中读取 system_prompt"""
        try:
            from easy_agent.services.agent_manager import get_agent_config

            _cfg = get_agent_config()
            if _cfg:
                return _cfg.get("system_prompt", "") or ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _resolve_memories_dir(username: str) -> str:
        """推导用户长期记忆目录：{memories_dir}/{username}/"""
        try:
            from easy_agent.config import Config

            return str(Config.get_user_memories_dir(username))
        except Exception:
            return ""

    def _load_existing(self) -> Optional[dict]:
        """加载已存在的会话日志文件；文件不存在或损坏返回 None。"""
        if not self.log_file.exists():
            return None
        try:
            with open(self.log_file, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, OSError):
            logger.warning(f"会话日志文件损坏，重新初始化: {self.log_file}")
            return None

    def _flush(self):
        self._data["updated_at"] = datetime.now().isoformat()
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def set_system_prompt(self, prompt: str):
        self._data["system_prompt"] = prompt
        self._flush()

    def log_user_message(
        self, message: str, files: Optional[list] = None, message_id: str = ""
    ):
        entry = {
            "type": "user_message",
            "timestamp": datetime.now().isoformat(),
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
        self._data["entries"].append(entry)
        self._flush()

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
            "timestamp": datetime.now().isoformat(),
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
        self._data["entries"].append(entry)
        self._flush()

    def log_thinking(
        self,
        content: str,
        step: int = 0,
        duration: Optional[float] = None,
        message_id: str = "",
    ):
        entry = {
            "type": "thinking",
            "timestamp": datetime.now().isoformat(),
            "message_id": message_id,
            "step": step,
            "content": content,
        }
        if duration is not None:
            entry["duration"] = duration
        self._data["entries"].append(entry)
        self._flush()

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
            "timestamp": datetime.now().isoformat(),
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
        self._data["entries"].append(entry)
        self._flush()

    def log_context_compression(
        self, summary: str, original_count: int, compressed_count: int
    ):
        entry = {
            "type": "context_compression",
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "original_message_count": original_count,
            "compressed_message_count": compressed_count,
        }
        self._data["entries"].append(entry)
        self._flush()

    def log_error(self, error: str, context: str = ""):
        entry = {
            "type": "error",
            "timestamp": datetime.now().isoformat(),
            "error": error,
            "context": context,
        }
        self._data["entries"].append(entry)
        self._flush()
