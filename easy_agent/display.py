"""CLI display helpers for terminal output"""

import json


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


def print_step_header(step_num: int, max_steps: int):
    BOX_WIDTH = 58
    print(f"\n{Colors.DIM}╭{'─' * BOX_WIDTH}╮{Colors.RESET}")
    step_text = f"💭 Step {step_num}/{max_steps}"
    padding = max(0, BOX_WIDTH - 4 - len(step_text))
    print(f"{Colors.DIM}│{Colors.RESET} {Colors.BRIGHT_MAGENTA}{step_text}{Colors.RESET}{' ' * padding}{Colors.DIM}│{Colors.RESET}")
    print(f"{Colors.DIM}╰{'─' * BOX_WIDTH}╯{Colors.RESET}")


def print_thinking(content: str, step: int = 0):
    step_info = f" (Step {step})" if step > 0 else ""
    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}🧠 Thinking{step_info}:{Colors.RESET}")
    for line in content.strip().split('\n'):
        print(f"{Colors.DIM}{line}{Colors.RESET}")


def print_tool_call(tool_name: str, arguments: dict, step: int = 0):
    args_str = json.dumps(arguments, indent=4, ensure_ascii=False)
    lines = args_str.split('\n')
    step_info = f" (Step {step})" if step > 0 else ""
    print(f"\n{Colors.BOLD}{Colors.BRIGHT_YELLOW}🔧 Tool Call{step_info}:{Colors.RESET} {tool_name}")
    print(f"{Colors.DIM}   Arguments:{Colors.RESET}")
    for line in lines:
        print(f"{Colors.DIM}   {line}{Colors.RESET}")


def print_tool_result(content: str, success: bool = True, step: int = 0):
    step_info = f" (Step {step})" if step > 0 else ""
    if success:
        result_icon = f"{Colors.GREEN}✓ Result{step_info}:{Colors.RESET}"
    else:
        result_icon = f"{Colors.BRIGHT_RED}✗ Result{step_info} (FAILED):{Colors.RESET}"
    if len(content) > 500:
        preview = content[:500] + f"\n... ({len(content)} chars total)"
        print(f"\n{result_icon}")
        print(f"{Colors.DIM}{preview}{Colors.RESET}")
    else:
        print(f"\n{result_icon}")
        print(f"{Colors.DIM}{content}{Colors.RESET}")


def print_step_time(step_num: int, step_time: float, total_time: float):
    print(f"\n{Colors.DIM}⏱️  Step {step_num} completed in {step_time:.2f}s (total: {total_time:.2f}s){Colors.RESET}")


def print_assistant_response(content: str):
    print(f"\n{Colors.BOLD}{Colors.BRIGHT_BLUE}🤖 Assistant:{Colors.RESET}")
    print(content)
