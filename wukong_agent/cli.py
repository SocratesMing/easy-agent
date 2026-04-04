"""Wukong Agent - Interactive Runtime Example

Usage:
    wukong-agent [--workspace DIR] [--task TASK]

Examples:
    wukong-agent                              # Use current directory as workspace (interactive mode)
    wukong-agent --workspace /path/to/dir     # Use specific workspace directory (interactive mode)
    wukong-agent --task "create a file"       # Execute a task non-interactively
"""

import argparse
import asyncio
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML

from .agent import WukongAgent, Colors
from .config import Config


def calculate_display_width(text: str) -> int:
    """Calculate display width of text accounting for ANSI codes and CJK characters"""
    import re
    ansi_escape = re.compile(r'\033\[[0-9;]*m')
    clean_text = ansi_escape.sub('', text)
    width = 0
    for char in clean_text:
        if ord(char) > 0x2E80:
            width += 2
        else:
            width += 1
    return width


class ColorsCLI:
    """Terminal color definitions for CLI"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


C = ColorsCLI()


def get_log_directory() -> Path:
    """Get the log directory path."""
    return Path(".wukong-agent") / "log"


def show_log_directory(open_file_manager: bool = True) -> None:
    """Show log directory contents and optionally open file manager.

    Args:
        open_file_manager: Whether to open the system file manager
    """
    log_dir = get_log_directory()

    print(f"\n{C.BRIGHT_CYAN}📁 Log Directory: {log_dir}{C.RESET}")

    if not log_dir.exists() or not log_dir.is_dir():
        print(f"{C.RED}Log directory does not exist: {log_dir}{C.RESET}\n")
        return

    log_files = list(log_dir.glob("*.log"))

    if not log_files:
        print(f"{C.YELLOW}No log files found in directory.{C.RESET}\n")
        return

    log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    print(f"{C.DIM}{'─' * 60}{C.RESET}")
    print(f"{C.BOLD}{C.BRIGHT_YELLOW}Available Log Files (newest first):{C.RESET}")

    for i, log_file in enumerate(log_files[:10], 1):
        mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
        size = log_file.stat().st_size
        size_str = f"{size:,}" if size < 1024 else f"{size / 1024:.1f}K"
        print(f"  {C.GREEN}{i:2d}.{C.RESET} {C.BRIGHT_WHITE}{log_file.name}{C.RESET}")
        print(f"      {C.DIM}Modified: {mtime.strftime('%Y-%m-%d %H:%M:%S')}, Size: {size_str}{C.RESET}")

    if len(log_files) > 10:
        print(f"  {C.DIM}... and {len(log_files) - 10} more files{C.RESET}")

    print(f"{C.DIM}{'─' * 60}{C.RESET}")

    if open_file_manager:
        _open_directory_in_file_manager(log_dir)

    print()


def _open_directory_in_file_manager(directory: Path) -> None:
    """Open directory in system file manager (cross-platform)."""
    system = platform.system()

    try:
        if system == "Darwin":
            subprocess.run(["open", str(directory)], check=False)
        elif system == "Windows":
            subprocess.run(["explorer", str(directory)], check=False)
        elif system == "Linux":
            subprocess.run(["xdg-open", str(directory)], check=False)
    except FileNotFoundError:
        print(f"{C.YELLOW}Could not open file manager. Please navigate manually.{C.RESET}")
    except Exception as e:
        print(f"{C.YELLOW}Error opening file manager: {e}{C.RESET}")


def read_log_file(filename: str) -> None:
    """Read and display a specific log file.

    Args:
        filename: The log filename to read
    """
    log_dir = get_log_directory()
    log_file = log_dir / filename

    if not log_file.exists() or not log_file.is_file():
        print(f"\n{C.RED}❌ Log file not found: {log_file}{C.RESET}\n")
        return

    print(f"\n{C.BRIGHT_CYAN}📄 Reading: {log_file}{C.RESET}")
    print(f"{C.DIM}{'─' * 80}{C.RESET}")

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            content = f.read()
        print(content)
        print(f"{C.DIM}{'─' * 80}{C.RESET}")
        print(f"\n{C.GREEN}✅ End of file{C.RESET}\n")
    except Exception as e:
        print(f"\n{C.RED}❌ Error reading file: {e}{C.RESET}\n")


def print_banner():
    """Print welcome banner with proper alignment"""
    BOX_WIDTH = 60
    banner_text = f"{C.BOLD}🐵 Wukong Agent - DeepAgents Powered AI Assistant{C.RESET}"
    banner_width = calculate_display_width(banner_text)

    total_padding = BOX_WIDTH - banner_width
    left_padding = total_padding // 2
    right_padding = total_padding - left_padding

    print()
    print(f"{C.BOLD}{C.BRIGHT_CYAN}╔{'═' * BOX_WIDTH}╗{C.RESET}")
    print(
        f"{C.BOLD}{C.BRIGHT_CYAN}║{C.RESET}{' ' * left_padding}{banner_text}{' ' * right_padding}{C.BOLD}{C.BRIGHT_CYAN}║{C.RESET}"
    )
    print(f"{C.BOLD}{C.BRIGHT_CYAN}╚{'═' * BOX_WIDTH}╝{C.RESET}")
    print()


def print_help():
    """Print help information"""
    help_text = f"""
{C.BOLD}{C.BRIGHT_YELLOW}Available Commands:{C.RESET}
  {C.BRIGHT_GREEN}/help{C.RESET}      - Show this help message
  {C.BRIGHT_GREEN}/clear{C.RESET}     - Clear session history (keep system prompt)
  {C.BRIGHT_GREEN}/exit{C.RESET}      - Exit program (also: exit, quit, q)

{C.BOLD}{C.BRIGHT_YELLOW}Keyboard Shortcuts:{C.RESET}
  {C.BRIGHT_CYAN}Ctrl+C{C.RESET}     - Exit program
  {C.BRIGHT_CYAN}Ctrl+U{C.RESET}     - Clear current input line
  {C.BRIGHT_CYAN}Ctrl+L{C.RESET}     - Clear screen
  {C.BRIGHT_CYAN}Tab{C.RESET}        - Auto-complete commands
  {C.BRIGHT_CYAN}↑/↓{C.RESET}        - Browse command history
  {C.BRIGHT_CYAN}→{C.RESET}          - Accept auto-suggestion

{C.BOLD}{C.BRIGHT_YELLOW}Usage:{C.RESET}
  - Enter your task directly, Agent will help you complete it
  - Agent remembers all conversation content in this session
  - Press {C.BRIGHT_CYAN}Enter{C.RESET} to submit your message

"""
    print(help_text)


def print_session_info(workspace_dir: Path, model: str, tools_count: int = 0):
    """Print session information with proper alignment"""
    BOX_WIDTH = 58

    def print_info_line(text: str):
        """Print a single info line with proper padding"""
        text_width = calculate_display_width(text)
        padding = max(0, BOX_WIDTH - 1 - text_width)
        print(f"{C.DIM}│{C.RESET} {text}{' ' * padding}{C.DIM}│{C.RESET}")

    print(f"{C.DIM}┌{'─' * BOX_WIDTH}┐{C.RESET}")

    header_text = f"{C.BRIGHT_CYAN}Session Info{C.RESET}"
    header_width = calculate_display_width(header_text)
    header_padding_total = BOX_WIDTH - 1 - header_width
    header_padding_left = header_padding_total // 2
    header_padding_right = header_padding_total - header_padding_left
    print(f"{C.DIM}│{C.RESET} {' ' * header_padding_left}{header_text}{' ' * header_padding_right}{C.DIM}│{C.RESET}")

    print(f"{C.DIM}├{'─' * BOX_WIDTH}┤{C.RESET}")

    print_info_line(f"Model: {model}")
    print_info_line(f"Workspace: {workspace_dir}")
    print_info_line(f"Available Tools: {tools_count} tools")

    print(f"{C.DIM}└{'─' * BOX_WIDTH}┘{C.RESET}")
    print()
    print(f"{C.DIM}Type {C.BRIGHT_GREEN}/help{C.DIM} for help, {C.BRIGHT_GREEN}/exit{C.DIM} to quit{C.RESET}")
    print()


def parse_args() -> argparse.Namespace:
    """Parse command line arguments

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Wukong Agent - AI assistant powered by DeepAgents framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  wukong-agent                              # Use current directory as workspace
  wukong-agent --workspace /path/to/dir     # Use specific workspace directory
  wukong-agent --task "create a file"       # Execute a task non-interactively
        """,
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=None,
        help="Workspace directory (default: current directory)",
    )
    parser.add_argument(
        "--task",
        "-t",
        type=str,
        default=None,
        help="Execute a task non-interactively and exit",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="wukong-agent 0.1.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    log_parser = subparsers.add_parser("log", help="Show log directory or read log files")
    log_parser.add_argument(
        "filename",
        nargs="?",
        default=None,
        help="Log filename to read (optional, shows directory if omitted)",
    )

    return parser.parse_args()


def load_system_prompt(config: Config) -> str:
    """Load system prompt from configuration

    Args:
        config: Configuration object

    Returns:
        System prompt string
    """
    system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)

    if system_prompt_path and system_prompt_path.exists():
        with open(system_prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    return """You are a helpful AI assistant powered by DeepAgents framework.
You can help users with various tasks including coding, writing, analysis, and more.
Be concise, accurate, and helpful in your responses."""


def discover_skills(skills_dir: str | None = None) -> list[dict]:
    """Discover and load skills from the skills directory

    Args:
        skills_dir: Path to skills directory (default: ./skills or wukong_agent/skills)

    Returns:
        List of skill info dictionaries with name and path
    """
    skills = []
    seen_names = set()

    search_paths = []

    if skills_dir:
        search_paths.append(Path(skills_dir))

    search_paths.extend([
        Path("skills"),
        Path("wukong_agent") / "skills",
        Path(__file__).parent / "skills",
    ])

    for search_path in search_paths:
        if not search_path.exists():
            continue

        for skill_dir in sorted(search_path.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            if skill_name in seen_names:
                continue

            skill_md = skill_dir / "SKILL.md"
            skill_readme = skill_dir / "README.md"

            if skill_md.exists() or skill_readme.exists():
                seen_names.add(skill_name)
                skills.append({
                    "name": skill_name,
                    "path": str(skill_dir.absolute()),
                    "description_file": "SKILL.md" if skill_md.exists() else "README.md",
                })

    return skills


def print_skills_info(skills: list[dict]):
    """Print discovered skills information

    Args:
        skills: List of skill dictionaries
    """
    if not skills:
        print(f"{C.YELLOW}⚠️  No skills found{C.RESET}")
        return

    print(f"\n{C.DIM}Loading Claude Skills...{C.RESET}")
    print(f"{C.GREEN}✅ Discovered {len(skills)} Claude Skills{C.RESET}")

    for i, skill in enumerate(skills, 1):
        print(f"   {C.DIM}{i:2d}.{C.RESET} {C.BRIGHT_WHITE}{skill['name']}{C.RESET}")

    print()


async def interactive_mode(agent: WukongAgent, config: Config):
    """Run agent in interactive mode

    Args:
        agent: WukongAgent instance
        config: Configuration object
    """
    history_path = Path(".wukong-agent") / "history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    session = PromptSession(
        history=FileHistory(str(history_path)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=WordCompleter(['/help', '/clear', '/exit', '/log'], ignore_case=True),
        style=Style.from_dict({
            'prompt': '#00ffff',
            'completion-menu.completion': '#ffffff',
            'completion-menu.completion.current': '#00ff00',
        }),
    )

    print_session_info(
        workspace_dir=Path(config.agent.workspace_dir),
        model=config.llm.model,
        tools_count=agent.get_tools_count(),
    )

    while True:
        try:
            user_input = await session.prompt_async(HTML(' <ansigreen>👤 You:</ansigreen> '))

            if not user_input.strip():
                continue

            user_input = user_input.strip()

            if user_input.lower() in ['/exit', 'exit', 'quit', 'q']:
                print(f"\n{C.BRIGHT_CYAN}👋 Goodbye!{C.RESET}\n")
                break

            if user_input == '/help':
                print_help()
                continue

            if user_input == '/clear':
                print(f"\n{C.BRIGHT_YELLOW}🧹 Session cleared. Starting fresh...{C.RESET}\n")
                continue

            if user_input.startswith('/log'):
                parts = user_input.split(maxsplit=1)
                if len(parts) > 1:
                    read_log_file(parts[1])
                else:
                    show_log_directory()
                continue

            print()

            response = await agent.run(user_input)

            print()

        except KeyboardInterrupt:
            print(f"\n\n{C.BRIGHT_YELLOW}⚠️  Interrupted. Type /exit to quit.{C.RESET}\n")
        except EOFError:
            print(f"\n\n{C.BRIGHT_CYAN}👋 Goodbye!{C.RESET}\n")
            break


async def non_interactive_mode(agent: WukongAgent, task: str):
    """Run agent in non-interactive mode

    Args:
        agent: WukongAgent instance
        task: Task to execute
    """
    response = await agent.run(task)
    print()
    return response


async def main_async():
    """Main async entry point"""
    args = parse_args()

    if args.command == "log":
        if args.filename:
            read_log_file(args.filename)
        else:
            show_log_directory()
        return

    try:
        config = Config.load()
    except FileNotFoundError as e:
        print(f"{Colors.BRIGHT_RED}❌ Configuration Error:{Colors.RESET} {e}")
        print(f"\n{Colors.YELLOW}Please create a config.yaml file in wukong_agent/config/ directory.{Colors.RESET}")
        sys.exit(1)
    except ValueError as e:
        print(f"{Colors.BRIGHT_RED}❌ Configuration Error:{Colors.RESET} {e}")
        sys.exit(1)

    if args.workspace:
        config.agent.workspace_dir = args.workspace

    system_prompt = load_system_prompt(config)

    print_banner()

    print(f"{C.GREEN}✅ Model: {config.llm.model} ({config.llm.provider}){C.RESET}")
    print(f"{C.GREEN}✅ Workspace: {Path(config.agent.workspace_dir).absolute()}{C.RESET}")
    print(f"{C.GREEN}✅ Framework: DeepAgents (LangChain){C.RESET}\n")

    skills = discover_skills()
    print_skills_info(skills)

    skills_paths = [skill['path'] for skill in skills] if skills else None

    if skills_paths:
        print(f"{C.DIM}Skills paths for DeepAgents:{C.RESET}")
        for sp in skills_paths:
            print(f"{C.DIM}  - {sp}{C.RESET}")

    agent = WukongAgent(config=config, system_prompt=system_prompt, skills=skills_paths)

    print(f"{C.GREEN}✅ Backend: {agent.backend_type}{C.RESET}")

    tools_info = agent.tools_info
    print(f"{C.DIM}Loading DeepAgents built-in tools...{C.RESET}")

    for tool in tools_info:
        tool_name = tool["name"]
        tool_category = tool.get("category", "")
        tool_desc = tool.get("description", "")

        if tool_category == "planning":
            print(f"{C.GREEN}✅ Loaded {tool_name} tool (task planning){C.RESET}")
        elif tool_category == "filesystem":
            print(f"{C.GREEN}✅ Loaded {tool_name} tool (file operation){C.RESET}")
        elif tool_category == "execution":
            print(f"{C.GREEN}✅ Loaded {tool_name} tool (command execution){C.RESET}")
        elif tool_category == "subagent":
            print(f"{C.GREEN}✅ Loaded {tool_name} tool (subagent spawning){C.RESET}")
        else:
            print(f"{C.GREEN}✅ Loaded {tool_name} tool{C.RESET}")

    print(f"{C.GREEN}✅ Total: {agent.get_tools_count()} tools loaded{C.RESET}")

    system_prompt_path = Config.find_config_file(config.agent.system_prompt_path)
    if system_prompt_path:
        print(f"{C.GREEN}✅ Loaded system prompt (from: {system_prompt_path}){C.RESET}")
    print()

    if args.task:
        await non_interactive_mode(agent, args.task)
    else:
        await interactive_mode(agent, config)


def main():
    """Main entry point"""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
