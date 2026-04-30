"""Per-session conversation logging - stores complete JSON logs"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SESSION_LOG_DIR = Path("logs") / "sessions"


def ensure_log_dir():
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)


class SessionLogger:
    """Logs full conversation as structured JSON per session.

    Stores: system prompt, user messages, assistant responses,
    thinking blocks, tool calls with IDs/args/results, context compression events.
    """

    def __init__(self, session_id: str, username: str = "",
                 workspace: str = "", system_prompt: str = ""):
        self.session_id = session_id
        ensure_log_dir()
        self.log_file = SESSION_LOG_DIR / f"{session_id}.json"

        # 初始化日志结构
        self._data: dict[str, Any] = {
            "session_id": session_id,
            "username": username,
            "workspace": workspace,
            "system_prompt": system_prompt,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "entries": [],
        }
        self._flush()

    def _flush(self):
        self._data["updated_at"] = datetime.now().isoformat()
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def set_system_prompt(self, prompt: str):
        self._data["system_prompt"] = prompt
        self._flush()

    def log_user_message(self, message: str, files: Optional[list] = None,
                         message_id: str = ""):
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

    def log_assistant_response(self, content: str, thinking: Optional[str] = None,
                                thinking_duration: Optional[float] = None,
                                tool_calls: Optional[list] = None,
                                message_id: str = ""):
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

    def log_tool_call(self, tool_name: str, tool_call_id: str,
                       arguments: dict, result: str = "",
                       success: bool = True, duration: Optional[float] = None,
                       step: int = 0, message_id: str = ""):
        self._data["entries"].append({
            "type": "tool_call",
            "timestamp": datetime.now().isoformat(),
            "message_id": message_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "result": result,
            "success": success,
            "duration": duration,
            "step": step,
        })
        self._flush()

    def log_thinking(self, content: str, step: int = 0,
                      duration: Optional[float] = None, message_id: str = ""):
        self._data["entries"].append({
            "type": "thinking",
            "timestamp": datetime.now().isoformat(),
            "message_id": message_id,
            "content": content,
            "step": step,
            "duration": duration,
        })
        self._flush()

    def log_system(self, message: str):
        self._data["entries"].append({
            "type": "system",
            "timestamp": datetime.now().isoformat(),
            "content": message,
        })
        self._flush()

    def log_context_compression(self, summary: str, original_length: int,
                                 compressed_length: int):
        self._data["entries"].append({
            "type": "context_compression",
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "original_length": original_length,
            "compressed_length": compressed_length,
        })
        self._flush()

    def get_full_log(self) -> dict:
        return self._data

    @staticmethod
    def get_session_log_path(session_id: str) -> Path:
        return SESSION_LOG_DIR / f"{session_id}.json"

    @staticmethod
    def get_all_session_logs() -> list[dict]:
        ensure_log_dir()
        logs = []
        for f in sorted(SESSION_LOG_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
            logs.append({
                "session_id": f.stem,
                "path": str(f),
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
        return logs
