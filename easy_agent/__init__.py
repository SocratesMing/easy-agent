"""Easy Agent - AI智能体框架。

包入口只放版本号：导入任何 ``easy_agent.*`` 子模块都会先执行这里，在这里导入会让
``import easy_agent.bootstrap.env`` 这样的小模块也连带加载整个 agent 核心。类和函数请从
定义它们的模块导入，例如 ``easy_agent.agent.EasyAgent``、``easy_agent.bootstrap.config.Config``
（docs/design/2026-10-20-services-subpackages-and-layering.md §7）。
"""

__version__ = "0.1.0"
