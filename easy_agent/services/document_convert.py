"""文档转换兼容重导出（实现已下沉至 ``easy_agent.utils.document_convert`` 以符合分层门禁）。"""

from easy_agent.utils.document_convert import (  # noqa: F401 - 兼容旧导入路径
    CACHE_DIR,
    SUPPORTED_EXT,
    _find_soffice,
    convert_office_document,
    convert_to_pdf,
)

__all__ = [
    "CACHE_DIR",
    "SUPPORTED_EXT",
    "convert_office_document",
    "convert_to_pdf",
]
