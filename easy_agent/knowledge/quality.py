"""Deterministic quality gates for knowledge answers and citations."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import Evidence


_CITATION = re.compile(r"\[知识依据(\d+)\]")


def validate_answer_citations(
    answer: str, evidence: Sequence[Evidence], *, no_answer_text: str
) -> tuple[str, list[str]]:
    answer = answer.strip()
    if not evidence:
        return no_answer_text, []
    references = [int(value) for value in _CITATION.findall(answer)]
    if not answer or not references:
        return no_answer_text, ["KNOWLEDGE_ANSWER_MISSING_CITATION"]
    if any(value < 1 or value > len(evidence) for value in references):
        return no_answer_text, ["KNOWLEDGE_ANSWER_INVALID_CITATION"]
    return answer, []


__all__ = ["validate_answer_citations"]
