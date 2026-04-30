"""Easy Agent - Web Server entry point"""
import sys
from pathlib import Path

root = Path(__file__).parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from easy_agent.web_runner import run_web

if __name__ == "__main__":
    run_web()
