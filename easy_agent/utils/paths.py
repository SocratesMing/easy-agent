"""项目根目录：后端解析 data/、frontend-vue2/dist/、experts/ 等相对路径时的基准。

按代码位置计算，与进程工作目录无关（uvicorn、systemd、IDE 启动时的 cwd 各不相同）。
"""

from pathlib import Path

# easy_agent/utils/paths.py -> easy_agent/utils -> easy_agent -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
