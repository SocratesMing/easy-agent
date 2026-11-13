#!/usr/bin/env python3
"""知识库检索离线评测（需要可用的 RAGFlow 与主库）。

用途：用一批真实问题回放检索链路，量化「召回得准不准」，为调阈值、接
rerank、改切块提供对照基线。脚本只做检索与指标计算，不调用对话模型。

指标：
- Recall@k：应命中文档中，有多少出现在前 k 条证据里（按文档去重）。
- MRR：第一条应命中文档在证据列表中的排名倒数。
- 命中率：至少命中一篇应命中文档的问题占比。
- 拒答率：标注 should_refuse=true 的问题，返回空证据视为通过。
- 引用准确率：golden 条目可选提供 answer 文本，解析其中的 [知识依据N]，
  校验编号是否越界、指向的文档是否属于应命中文档；未提供 answer 时跳过。

用法：
    uv run python scripts/dev/knowledge_eval/run.py \
        --golden-set scripts/dev/knowledge_eval/golden_set.example.jsonl \
        --username admin --top-k 8

golden_set.jsonl 每行一个 JSON 对象：
    {
      "id": "q-001",                       # 可选，缺省按行号
      "question": "问题原文",               # 必填
      "base_ids": ["知识库ID"],             # 必填（可用 --base-id 统一覆盖）
      "expected_document_names": ["x.pdf"], # 应命中文档名（与 ID 二选一/可同时）
      "expected_document_ids": ["doc-id"],  # 应命中文档本地 ID
      "should_refuse": false,               # true = 期望返回空证据
      "answer": "含 [知识依据1] 的回答",      # 可选，用于引用准确率
      "notes": "备注"                        # 可选
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.bootstrap.initialization import initialize_runtime  # noqa: E402
from easy_agent.db.database import Database, init_database  # noqa: E402
from easy_agent.knowledges.api import KnowledgePrincipal  # noqa: E402
from easy_agent.knowledges.core.config import KnowledgeConfig  # noqa: E402
from easy_agent.knowledges.core.repository import KnowledgeRepository  # noqa: E402
from easy_agent.knowledges.ragflow import RagflowClient  # noqa: E402
from easy_agent.knowledges.service import (  # noqa: E402
    KnowledgeService,
    KnowledgeServiceError,
)

_CITATION_RE = re.compile(r"\[知识依据(\d+)\]")


def _load_principal(db: Database, username: str) -> KnowledgePrincipal:
    """按用户名加载评测身份（权限校验与线上一致）。"""

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT user_id, username, "
            "COALESCE(NULLIF(department_id, ''), organization_id) AS department_id "
            "FROM users WHERE username=?",
            (username,),
        )
        row = cursor.fetchone()
    if row is None:
        raise SystemExit(f"用户不存在: {username}")
    data = dict(row)
    return KnowledgePrincipal(
        user_id=str(data["user_id"]),
        username=str(data["username"]),
        department_id=str(data.get("department_id") or "") or None,
    )


def _load_entries(path: Path, base_ids_override: list[str]) -> list[dict[str, Any]]:
    """读取 JSONL 评测集；空行与 # 开头行忽略。"""

    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry = json.loads(line)
        entry.setdefault("id", f"line-{index}")
        if not str(entry.get("question") or "").strip():
            raise SystemExit(f"第 {index} 行缺少 question")
        if base_ids_override:
            entry["base_ids"] = list(base_ids_override)
        if not entry.get("base_ids"):
            raise SystemExit(f"第 {index} 行缺少 base_ids（或用 --base-id 指定）")
        if not entry.get("should_refuse") and not (
            entry.get("expected_document_names") or entry.get("expected_document_ids")
        ):
            raise SystemExit(
                f"第 {index} 行为可回答问题，但未填写 expected_document_names/ids"
            )
        entries.append(entry)
    if not entries:
        raise SystemExit("评测集为空")
    return entries


def _citation_accuracy(
    answer: str | None,
    evidence_names: list[str],
    expected_names: set[str],
    expected_ids: set[str],
    evidence_ids: list[str],
) -> float | None:
    """校验 [知识依据N] 是否越界、是否指向应命中文档；无引用返回 0。"""

    if not answer:
        return None
    matches = _CITATION_RE.findall(answer)
    if not matches:
        return 0.0
    correct = 0
    for raw in matches:
        index = int(raw) - 1
        if not 0 <= index < len(evidence_names):
            continue
        if (not expected_names and not expected_ids) or (
            evidence_names[index] in expected_names
            or evidence_ids[index] in expected_ids
        ):
            correct += 1
    return correct / len(matches)


async def _retrieve_for_entry(
    service: KnowledgeService,
    principal: KnowledgePrincipal,
    question: str,
    base_ids: list[str],
    top_k: int,
) -> list[Any]:
    """按知识库并行检索并合并去重（与线上会话检索同一实现）。"""

    results = await asyncio.gather(
        *(
            service._retrieve_base_evidence(
                base_id=base_id,
                principal=principal,
                question=question,
                document_ids=None,
                top_n=top_k,
                request_id="knowledge-eval",
            )
            for base_id in base_ids
        )
    )
    # 与线上会话检索保持同一合并规则（按库配额 + 全局分数）
    return service._merge_session_evidence(
        list(results),
        top_n=top_k,
        per_base_quota=service.config.retrieval.per_base_quota,
    )


async def run(args: argparse.Namespace) -> int:
    app_config = initialize_runtime().config
    if app_config is None:
        print("ERROR: 配置未加载", file=sys.stderr)
        return 2
    config = KnowledgeConfig.load()
    if not config.enabled:
        print("知识库模块未启用（knowledge.enabled=false），退出")
        return 2

    import easy_agent.knowledges.personnel  # noqa: F401 - schema 注册

    db = init_database(app_config.database.model_dump())
    principal = _load_principal(db, args.username)
    repository = KnowledgeRepository(db)
    ragflow = RagflowClient(config.base_url, config.api_key.get_secret_value())
    service = KnowledgeService(repository, ragflow, config)
    entries = _load_entries(Path(args.golden_set), args.base_id)

    rows: list[dict[str, Any]] = []
    try:
        for entry in entries:
            question = str(entry["question"])
            try:
                evidence = await _retrieve_for_entry(
                    service, principal, question, list(entry["base_ids"]), args.top_k
                )
                error = None
            except KnowledgeServiceError as exc:
                evidence = []
                error = f"{exc.code}: {exc.message}"
            names = [item.document_name for item in evidence]
            ids = [item.document_id for item in evidence]
            expected_names = {
                str(item) for item in (entry.get("expected_document_names") or [])
            }
            expected_ids = {
                str(item) for item in (entry.get("expected_document_ids") or [])
            }
            should_refuse = bool(entry.get("should_refuse"))
            if should_refuse:
                row = {
                    "id": entry["id"],
                    "question": question,
                    "should_refuse": True,
                    "refused": not evidence,
                    "passed": not evidence,
                    "error": error,
                }
            else:
                positions: dict[str, int] = {}
                for rank, (name, doc_id) in enumerate(zip(names, ids), 1):
                    positions.setdefault(f"name:{name}", rank)
                    positions.setdefault(f"id:{doc_id}", rank)
                ranks = [
                    positions[f"name:{name}"]
                    for name in expected_names
                    if f"name:{name}" in positions
                ]
                ranks += [
                    positions[f"id:{doc_id}"]
                    for doc_id in expected_ids
                    if f"id:{doc_id}" in positions
                ]
                expected_total = len(expected_names) + len(expected_ids)
                recall = len(ranks) / expected_total if expected_total else 0.0
                row = {
                    "id": entry["id"],
                    "question": question,
                    "should_refuse": False,
                    "recall": recall,
                    "mrr": 1 / min(ranks) if ranks else 0.0,
                    "hit": bool(ranks),
                    "evidence_count": len(evidence),
                    "evidence_names": names,
                    "citation_accuracy": _citation_accuracy(
                        entry.get("answer"), names, expected_names, expected_ids, ids
                    ),
                    "error": error,
                }
            rows.append(row)
            status = "PASS" if row.get("passed", row.get("hit")) else "FAIL"
            detail = (
                f"recall={row.get('recall', 0):.2f} mrr={row.get('mrr', 0):.2f}"
                if not row.get("should_refuse")
                else f"refused={row.get('refused')}"
            )
            print(f"[{status}] {entry['id']} {detail}  {question[:60]}")
    finally:
        await ragflow.close()

    answerable = [row for row in rows if not row.get("should_refuse")]
    refusals = [row for row in rows if row.get("should_refuse")]
    citations = [
        row["citation_accuracy"]
        for row in answerable
        if row.get("citation_accuracy") is not None
    ]
    summary = {
        "questions": len(rows),
        "answerable": len(answerable),
        "recall_at_k": mean([row["recall"] for row in answerable]) if answerable else 0.0,
        "mrr": mean([row["mrr"] for row in answerable]) if answerable else 0.0,
        "hit_rate": (
            sum(1 for row in answerable if row["hit"]) / len(answerable)
            if answerable
            else 0.0
        ),
        "refusal_accuracy": (
            sum(1 for row in refusals if row["refused"]) / len(refusals)
            if refusals
            else None
        ),
        "citation_accuracy": mean(citations) if citations else None,
        "errors": sum(1 for row in rows if row.get("error")),
    }
    print("\n===== 汇总 =====")
    for key, value in summary.items():
        if isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")

    output = Path(args.output) if args.output else Path(args.golden_set).with_name(
        "eval_report.json"
    )
    output.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n明细已写入 {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库检索离线评测")
    parser.add_argument(
        "--golden-set",
        default=str(Path(__file__).with_name("golden_set.example.jsonl")),
        help="评测集 JSONL 路径",
    )
    parser.add_argument("--username", required=True, help="用于权限校验的用户名")
    parser.add_argument(
        "--base-id",
        action="append",
        default=[],
        help="统一覆盖评测集里的 base_ids，可重复传入",
    )
    parser.add_argument("--top-k", type=int, default=8, help="证据条数（默认 8）")
    parser.add_argument("--output", default="", help="明细输出路径（默认评测集同目录）")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
