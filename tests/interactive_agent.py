"""Interactive DeepSeek agent using DeepAgents framework.

Usage:
    uv run python tests/interactive_agent.py
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from langchain_openai import ChatOpenAI
from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend

# ── DeepSeek Configuration ──────────────────────────────────────
DEEPSEEK_API_KEY = "YOUR_API_KEY_HERE"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
# ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
WORKSPACE = PROJECT_ROOT / "workspace" / "agent_playground"
HISTORY_DIR = Path(__file__).parent / "chat_history"


def build_agent():
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    model = ChatOpenAI(
        api_key=DEEPSEEK_API_KEY,
        model=DEEPSEEK_MODEL,
        base_url=DEEPSEEK_BASE_URL,
        streaming=True,
    )

    # Single backend rooted at project root.
    # Virtual paths like /workspace/xxx resolve to PROJECT_ROOT/workspace/xxx
    backend = LocalShellBackend(root_dir=str(PROJECT_ROOT))

    return create_deep_agent(
        name="deepseek-agent",
        model=model,
        system_prompt=(
            "You are a helpful AI assistant powered by DeepSeek. "
            "Keep responses concise. Reply in the user's language."
            f"\n## Project root: {PROJECT_ROOT}"
            f"\n## Default workspace: {WORKSPACE}"
            "\n## OS: Windows"
            "\n- Shell commands: `dir`, `type`, `copy`, `del`, `mkdir`, `rmdir`"
            "\n- Python: `python` (NOT `python3`)"
            "\n## Rules"
            "\n- Always use virtual paths relative to project root, NEVER absolute Windows paths."
            "\n- Create/edit files under /workspace/agent_playground/ (e.g., /workspace/agent_playground/output.txt)."
            "\n- Search/read files across the project (e.g., /easy_agent/config.py, /tests/test_basic.py)."
        ),
        backend=backend,
    )


def save_session(filepath: Path, messages: list, metadata: dict):
    data = {
        "metadata": metadata,
        "messages": messages,
    }
    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    session_start = datetime.now()
    session_file = HISTORY_DIR / f"session_{session_start.strftime('%Y%m%d_%H%M%S')}.json"

    session_messages = []
    metadata = {
        "start_time": session_start.isoformat(),
        "model": DEEPSEEK_MODEL,
        "base_url": DEEPSEEK_BASE_URL,
        "project_root": str(PROJECT_ROOT),
        "workspace": str(WORKSPACE),
    }

    agent = build_agent()
    print(f"DeepSeek Agent ready | project root: {PROJECT_ROOT}")
    print(f"Session log: {session_file}")
    print("Type your message (Ctrl+C or 'quit' to exit)\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input or user_input.lower() in ("quit", "exit"):
            print("Bye!")
            break

        user_msg = {"role": "user", "content": user_input, "timestamp": datetime.now().isoformat()}
        session_messages.append(user_msg)

        print("Agent: ", end="", flush=True)
        response_text = ""
        tool_calls = []

        try:
            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": user_input}]},
                stream_mode="messages",
                subgraphs=True,
                version="v2",
            ):
                if chunk["type"] != "messages":
                    continue

                token, _ = chunk["data"]
                ns = chunk.get("ns", [])

                if any(s.startswith("tools:") for s in ns):
                    continue

                content = getattr(token, "content", "")
                if content:
                    text = str(content)
                    response_text += text
                    print(text, end="", flush=True)

                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    for tc in token.tool_call_chunks:
                        name = tc.get("name")
                        if name:
                            tool_calls.append(name)
                            print(f"\n  [tool: {name}]", end="", flush=True)

        except Exception as e:
            response_text = f"Error: {e}"
            print(f"\n{response_text}")

        print("\n")

        assistant_msg = {
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.now().isoformat(),
        }
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        session_messages.append(assistant_msg)

        metadata["end_time"] = datetime.now().isoformat()
        metadata["turns"] = len([m for m in session_messages if m["role"] == "user"])
        save_session(session_file, session_messages, metadata)


if __name__ == "__main__":
    asyncio.run(main())
