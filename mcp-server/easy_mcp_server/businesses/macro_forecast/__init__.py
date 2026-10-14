"""国内宏观经济指标预测 MCP 工具（报告 / 指标 / 多模型预测 / SHAP 归因）。

挂载路径：``/mcp/macro_forecast/``
"""

from .server import build

__all__ = ["build"]
