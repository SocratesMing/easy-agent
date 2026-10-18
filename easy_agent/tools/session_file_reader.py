"""LangChain tool：按需读取/重解析用户上传的文件正文。

背景
----
上传文件在 `api/sessions.py` 落盘时就地解析到会话工作区 `uploads/`。但首次解析
可能失败（典型：当时解析库不可用，如未装 pymupdf），工作区里只会留下失败占位。
原始文件保存在服务端 `uploadfiles/{file_id}_{原名}`（**工作区之外**，模型的
`grep`/`read_file` 工具读不到），模型自己既无法重试解析、也无法直接读源文件。

本工具就是补这个缺口：Agent 在清单里看到"解析失败/未解析"，或需要原始内容时
调用 `read_uploaded_file(文件名)`，由服务端到 `uploadfiles/` 重新解析源文件、
回填 `uploads/` 正文并更新清单，再把正文返回给模型。

约定：返回给模型的文案必须引导模型把来源表述为"用户上传的文件《原始文件名》"，
**不得向用户暴露 uploads/*.txt 等内部解析文件路径**（见 MANIFEST_HEADER）。
"""

import logging

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from easy_agent.services.session_uploads import ensure_parsed_text

logger = logging.getLogger("easy_agent.tools.session_file_reader")

# 单次返回给模型的正文上限：够模型定位答案，又不至于一次调用撑爆上下文；
# 需要更多时模型可以再调（配合工作区 grep 检索命中片段）。
DEFAULT_RETURN_CHARS = 20000
MAX_RETURN_CHARS = 60000


class ReadUploadedFileArgs(BaseModel):
    filename: str = Field(
        ...,
        description=(
            "上传文件的原始文件名（含扩展名），与 uploads/index.md 清单中的文件名一致，"
            "例如 '乐鑫科技2026年地三季度业绩预告.pdf'"
        ),
    )
    max_chars: int = Field(
        default=DEFAULT_RETURN_CHARS,
        ge=500,
        le=MAX_RETURN_CHARS,
        description="最多返回的正文字符数；正文更长时建议先用工作区 grep 定位再分段读取",
    )


class ReadUploadedFileTool(BaseTool):
    name: str = "read_uploaded_file"
    description: str = (
        "读取用户上传文件的解析正文。适用场景："
        "① uploads/index.md 清单中该文件标注为'解析失败/未解析'（服务端会重新解析源文件）；"
        "② grep/read_file 在 uploads/ 下找不到该文件正文；"
        "③ 需要某个上传文件的完整内容而非片段。"
        "注意：向用户展示来源时请说'来自您上传的文件《文件名》'，不要提及内部解析文件路径。"
    )
    args_schema: type = ReadUploadedFileArgs

    username: str = ""
    session_id: str = ""

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("此工具仅支持异步调用，请使用 _arun")

    async def _arun(self, filename: str, max_chars: int = DEFAULT_RETURN_CHARS) -> str:
        if not filename.strip():
            return "错误：filename 不能为空。请传入 uploads/index.md 清单中的文件名（含扩展名）。"

        from easy_agent.db import get_database

        db = get_database()
        try:
            parsed = ensure_parsed_text(self.session_id, self.username, filename, db)
        except Exception as e:
            logger.warning(
                f"[{self.session_id[-5:]}] read_uploaded_file 异常 | {filename} | {e}"
            )
            parsed = {"text": "", "status": "服务端读取异常"}
        text = (parsed.get("text") or "").strip()

        if not text:
            status = parsed.get("status") or "解析失败"
            logger.warning(
                f"[{self.session_id[-5:]}] read_uploaded_file 无法取得正文 | "
                f"{filename} | {status}"
            )
            return (
                f"《{filename}》当前无法提供正文（{status}）。\n"
                "请如实告知用户：该上传文件暂时无法解析出文本内容（可能是加密、扫描件、"
                "纯图片或服务端缺少解析库），建议用户确认文件可正常打开后重新上传；"
                "不要编造文件内容。"
            )

        if parsed.get("reparsed"):
            head = (
                f"《{filename}》此前解析失败，本次已重新解析成功"
                f"（来源：用户上传的原始文件，位于服务端 uploadfiles/ 目录）。\n\n"
            )
        else:
            head = f"《{filename}》解析正文（来源：用户上传的原始文件）。\n\n"

        if len(text) > max_chars:
            tail_hint = (
                f"\n\n[提示：以上为正文前 {max_chars} 字符，全文共 {len(text)} 字符；"
                f"正文副本在工作区 uploads/ 目录，可用 grep 定位关键词后用 read_file 读片段]"
            )
            return head + text[:max_chars] + tail_hint
        return head + text
