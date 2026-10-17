"""兼容垫片：streaming 实现已迁移到同目录 ``streaming/`` 包。

Python 的导入顺序是「同名包优先于同名模块」，因此运行时
``easy_agent.services.streaming`` 实际指向 ``streaming/__init__.py``；本文件
保留在仓库中，供仍按文件路径（``spec_from_file_location``）加载旧模块的工具使用，
并把包的公开符号重新导出，保证两种加载方式拿到同一批对象。

旧符号（含下划线私有名）全部仍可从 ``easy_agent.services.streaming`` 导入；
本文件不包含任何实现，避免出现第二份事实来源。
"""

from easy_agent.services.streaming import *  # noqa: F401,F403
