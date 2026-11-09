"""Agent run logger"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentLogger:
    """Agent run logger

    Responsible for recording the complete interaction process of each agent run, including:
    - LLM requests and responses
    - Tool calls in LLM responses
    """

    def __init__(self):
        """Initialize logger

        Logs are stored in .easy-agent/log/ directory
        """
        self.log_dir = Path(".easy-agent") / "log"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = None
        self.log_index = 0

    def start_new_run(self):
        """Start new run, create new log file"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"agent_run_{timestamp}.log"
        self.log_file = self.log_dir / log_filename
        self.log_index = 0

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(
                f"Easy Agent Run Log - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            f.write("=" * 80 + "\n\n")

    def log_request(self, messages: list[dict], tools: list[Any] | None = None):
        """Log LLM request

        Args:
            messages: Message list
            tools: Tool list (optional)
        """
        self.log_index += 1

        request_data = {
            "messages": messages,
            "tools": "DeepAgents built-in tools (see system prompt for details)",
        }

        content = "LLM Request:\n\n"
        content += json.dumps(request_data, indent=2, ensure_ascii=False)

        self._write_log("REQUEST", content)

    def log_response(
        self,
        content: str,
        thinking: str | None = None,
        tool_calls: list[dict] | None = None,
        finish_reason: str | None = None,
    ):
        """Log LLM response

        Args:
            content: Response content
            thinking: Thinking content (optional)
            tool_calls: Tool call list (optional)
            finish_reason: Finish reason (optional)
        """
        self.log_index += 1

        response_data = {
            "content": content,
        }

        if thinking:
            response_data["thinking"] = thinking

        if tool_calls:
            response_data["tool_calls"] = tool_calls

        if finish_reason:
            response_data["finish_reason"] = finish_reason

        log_content = "LLM Response:\n\n"
        log_content += json.dumps(response_data, indent=2, ensure_ascii=False)

        self._write_log("RESPONSE", log_content)

    def _write_log(self, log_type: str, content: str):
        if self.log_file is None:
            return

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S") + f".{now.microsecond:06d}"

        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(
                f"[{timestamp}]|INFO|FMQT|-||-|-|MainThread|agent_logger:0| [{self.log_index}] {log_type}\n"
            )
            f.write(content + "\n")
