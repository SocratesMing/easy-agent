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
    """Logs full conversation as structured JSON per session."""

    def __init__(self, session_id: str, username: str = "",
                 workspace: str = "", system_prompt: str = ""):
        self.session_id = session_id
        ensure_log_dir()
        self.log_file = SESSION_LOG_DIR / f"{session_id}.json"

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

    def log_thinking(self, content: str, step: int = 0,
                     duration: Optional[float] = None,
                     message_id: str = ""):
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

    def log_tool_call(self, tool_name: str, tool_call_id: str,
                      arguments: dict, result: str = "",
                      success: bool = True, duration: Optional[float] = None,
                      step: int = 0, message_id: str = ""):
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

    def log_context_compression(self, summary: str, original_count: int,
                                compressed_count: int):
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
