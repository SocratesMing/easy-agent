"""Agent 后端包：会话上下文路由、公共技能只读视图、框架产物目录与 shell / 沙箱执行后端。

包级只导出带虚拟路径翻译的 shell（``_PathTranslatingShell`` 是旧名）。
"""

from easy_agent.backends.shell import PathTranslatingShell, _PathTranslatingShell

__all__ = ["PathTranslatingShell", "_PathTranslatingShell"]
