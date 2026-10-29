"""SSE 流式响应：聊天、专家执行与流回放端点共用的 ``StreamingResponse`` 构造。"""

from fastapi.responses import StreamingResponse

# 不缓存，并关掉 nginx 等反向代理的响应缓冲（X-Accel-Buffering），事件才能逐条送到前端
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_response(stream) -> StreamingResponse:
    """把逐条产出 SSE 分片的异步生成器包装成 ``text/event-stream`` 响应。"""
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
