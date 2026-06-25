"""RAG 检索服务。

通过预留的 Python 脚本调用 RAG 检索接口（接口开发中）。
当前实现：
1. 优先尝试调用本地向量数据库（若已启用）
2. 否则调用预留的 RAG 脚本（scripts/rag_search.py，开发完成后放置）
3. 所有操作记录详细日志

接口调用规范（供 RAG 接口开发参考）：
  请求（POST）:
    {
      "query": "用户查询内容",
      "n_results": 5,
      "files": ["可选：上传文件路径列表"],
      "session_id": "会话ID",
      "username": "用户名"
    }
  响应:
    {
      "success": true,
      "results": [
        {"content": "检索到的文本片段", "score": 0.92, "source": "来源", "metadata": {}}
      ],
      "count": 3
    }
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("easy_agent.rag")

# RAG 脚本路径（预留，开发完成后放置）
RAG_SCRIPT_PATH = os.environ.get("RAG_SCRIPT_PATH", "scripts/rag_search.py")
# RAG 脚本调用超时（秒）
RAG_SCRIPT_TIMEOUT = 30


def search_knowledge_base(
    query: str,
    username: str = "default",
    session_id: str = "",
    files: Optional[list] = None,
    n_results: int = 5,
    http_request=None,
) -> dict:
    """调用 RAG 检索接口。

    检索顺序：
    1. 若 http_request 可用且向量数据库已启用，使用内置向量检索
    2. 否则调用预留的 RAG 脚本

    Args:
        query: 用户查询内容
        username: 用户名
        session_id: 会话ID
        files: 上传文件路径列表
        n_results: 返回结果数量
        http_request: FastAPI Request 对象（用于访问内置向量库）

    Returns:
        {"success": bool, "results": list, "count": int, "source": str}
    """
    start = time.time()
    logger.info(
        f"[RAG] 开始检索 | 用户: {username} | 会话: {session_id} | "
        f"查询: {query[:80]}{'...' if len(query) > 80 else ''} | 文件数: {len(files or [])}"
    )

    # 策略1：尝试内置向量数据库
    if http_request is not None:
        result = _search_via_vector_store(query, http_request, n_results)
        if result is not None:
            elapsed = time.time() - start
            logger.info(
                f"[RAG] 内置向量库检索完成 | 结果数: {result['count']} | 耗时: {elapsed:.2f}s"
            )
            return result

    # 策略2：调用预留 RAG 脚本
    result = _search_via_script(query, username, session_id, files, n_results)
    elapsed = time.time() - start
    if result["success"]:
        logger.info(
            f"[RAG] 脚本检索完成 | 结果数: {result['count']} | 耗时: {elapsed:.2f}s"
        )
    else:
        logger.warning(
            f"[RAG] 脚本检索失败 | 原因: {result.get('error', '未知')} | 耗时: {elapsed:.2f}s"
        )
    return result


def _search_via_vector_store(query: str, http_request, n_results: int) -> Optional[dict]:
    """通过内置向量数据库检索。"""
    try:
        vs = getattr(http_request.app.state, "vector_store", None)
        if not vs or not vs.is_ready:
            return None

        results = vs.search(query=query, n_results=n_results)
        formatted = []
        for r in results:
            formatted.append(
                {
                    "content": r.get("document", r.get("content", "")),
                    "score": r.get("distance", r.get("score", 0)),
                    "source": r.get("metadata", {}).get("source", "vector_store"),
                    "metadata": r.get("metadata", {}),
                }
            )
        return {"success": True, "results": formatted, "count": len(formatted), "source": "vector_store"}
    except Exception as e:
        logger.warning(f"[RAG] 内置向量库检索异常: {e}")
        return None


def _search_via_script(
    query: str,
    username: str,
    session_id: str,
    files: Optional[list],
    n_results: int,
) -> dict:
    """调用预留的 RAG Python 脚本。

    脚本接口（stdin 传入 JSON，stdout 返回 JSON）：
      输入: {"query": "...", "username": "...", "session_id": "...", "files": [...], "n_results": 5}
      输出: {"success": true, "results": [...]}
    """
    script_path = Path(RAG_SCRIPT_PATH)
    if not script_path.exists():
        logger.info(f"[RAG] 预留脚本不存在: {script_path}（接口开发中）")
        return {
            "success": False,
            "results": [],
            "count": 0,
            "source": "script",
            "error": f"RAG 脚本未部署: {script_path}",
        }

    payload = {
        "query": query,
        "username": username,
        "session_id": session_id,
        "files": files or [],
        "n_results": n_results,
    }

    try:
        proc = subprocess.run(
            ["python", str(script_path)],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=RAG_SCRIPT_TIMEOUT,
            encoding="utf-8",
        )

        if proc.returncode != 0:
            logger.error(f"[RAG] 脚本执行失败 | returncode: {proc.returncode} | stderr: {proc.stderr[:500]}")
            return {
                "success": False,
                "results": [],
                "count": 0,
                "source": "script",
                "error": proc.stderr[:500] or f"脚本退出码: {proc.returncode}",
            }

        data = json.loads(proc.stdout)
        results = data.get("results", [])
        return {
            "success": True,
            "results": results,
            "count": len(results),
            "source": "script",
        }
    except subprocess.TimeoutExpired:
        logger.error(f"[RAG] 脚本执行超时 ({RAG_SCRIPT_TIMEOUT}s)")
        return {
            "success": False,
            "results": [],
            "count": 0,
            "source": "script",
            "error": f"脚本执行超时",
        }
    except json.JSONDecodeError as e:
        logger.error(f"[RAG] 脚本返回非 JSON: {e}")
        return {
            "success": False,
            "results": [],
            "count": 0,
            "source": "script",
            "error": f"脚本返回格式错误: {e}",
        }
    except Exception as e:
        logger.error(f"[RAG] 脚本调用异常: {e}", exc_info=True)
        return {
            "success": False,
            "results": [],
            "count": 0,
            "source": "script",
            "error": str(e),
        }


def format_rag_context(rag_result: dict, original_query: str) -> str:
    """将 RAG 检索结果与用户原始输入整合，生成发送给 Agent 的增强内容。

    格式：
        [知识库检索结果]
        1. (score: 0.92) 来源: xxx
           内容片段...
        2. ...

        [用户原始问题]
        {original_query}
    """
    if not rag_result or not rag_result.get("success"):
        return original_query

    results = rag_result.get("results", [])
    if not results:
        return original_query

    lines = ["[知识库检索结果]"]
    for i, r in enumerate(results, 1):
        score = r.get("score", "")
        source = r.get("source", "")
        content = r.get("content", "").strip()
        if not content:
            continue
        header = f"{i}."
        if score != "":
            header += f" (相关度: {score})"
        if source:
            header += f" 来源: {source}"
        lines.append(header)
        lines.append(content)
        lines.append("")

    lines.append("[用户原始问题]")
    lines.append(original_query)

    return "\n".join(lines)
