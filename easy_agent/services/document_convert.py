"""文档转换兼容别名（实现已下沉至 ``easy_agent.utils.document_convert`` 以符合分层门禁）。"""

import sys

from easy_agent.utils import document_convert as _impl

# 保持模块对象身份一致，使既有测试对 easy_agent.services.document_convert.* 的
# monkeypatch 直接作用于 easy_agent.utils.document_convert。
sys.modules[__name__] = _impl
