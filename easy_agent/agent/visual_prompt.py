"""对话内可视化的提示词段：按 ``visual`` 配置决定教不教模型写图表 / 流程图 / 交互组件代码块。

前端把这三种代码块渲染成沙箱 iframe 里的卡片（见 ``api/viz.py`` 与
``docs/design/2026-11-12-chat-visuals.md``）。配置关掉某一类时，提示词里就不提它，
免得模型写出来以后用户只能看到源码。
"""

from __future__ import annotations

from easy_agent.bootstrap.config_models import VisualConfig

_HEADER = (
    "## 图表与流程图\n\n"
    "回答需要图表、流程图时，直接在正文里写下面的代码块，界面会把它渲染成卡片；"
    "不要写成文件，也不要让用户自己运行代码。"
)

_ECHARTS = (
    "- 数据图表：```echarts 代码块，内容是一个 ECharts option，必须是合法 JSON"
    "（双引号、不写函数和注释）。数据只能来自工具结果、上传文件或用户给出的数字，不要编造；"
    "图表后用一两句话说明结论。数据类工具的结果里已经带了 chart 字段时，界面会自动画出那张图，不要再重复输出。"
)

_MERMAID = "- 流程图、时序图、结构图：```mermaid 代码块（如 `flowchart TD`），节点文字简短。"

_WIDGET = (
    "- 需要交互（拖动滑块、切换参数、逐步演示）时：```widget 代码块，内容是一段 HTML"
    "（可含 <style>、<svg>、<script>），在隔离的沙箱里运行，不能联网、不能读写本地存储、不能弹窗。"
    "可以直接使用全局变量 echarts；颜色用 CSS 变量 var(--text-primary)、var(--text-secondary)、"
    "var(--border-color)、var(--bg-secondary)、var(--accent-color) 以适配深浅色主题；"
    "计算都在页面内用脚本完成，不要引用外部资源。需要让用户带着当前参数继续追问时，"
    "可以在按钮里调用 sendPrompt(文字)，它会把文字填进对话输入框。"
)

_FOOTER = "一个代码块只放一张图；文字就能说清楚时不要硬加图。"


def build_visual_prompt(visual: VisualConfig | None) -> str:
    """可视化提示词段；总开关关闭时返回空串（组装时整段跳过）。"""
    visual = visual if isinstance(visual, VisualConfig) else VisualConfig()
    if not visual.enabled:
        return ""
    bullets = [_ECHARTS, _MERMAID]
    if visual.html_widgets:
        bullets.append(_WIDGET)
    return "\n\n".join([_HEADER, "\n".join(bullets), _FOOTER])
