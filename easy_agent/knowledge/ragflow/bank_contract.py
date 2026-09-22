"""Request policy for the bank-customized RAG service contract.

This is the production contract.  Development compatibility with upstream
RAGFlow belongs in ``local_v017_bridge.py`` and must not leak into this module.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import KnowledgeConfig


def bank_request_headers(
    config: "KnowledgeConfig", operation_name: str
) -> dict[str, str]:
    """Build server-controlled headers required by the bank API."""

    headers = {"sys-code": config.bank_api.system_code}
    aaas = config.bank_api.aaas_retrieval
    if operation_name != "retrieve" or not aaas.enabled:
        return headers

    now = datetime.now().astimezone()
    transaction_timestamp = now.strftime("%Y%m%d%H%M%S%f")[:17]
    transaction_id = (
        f"{aaas.transaction_id_prefix}{transaction_timestamp}"
        f"{secrets.randbelow(10_000):04d}"
    )
    headers.update(
        {
            "stdAuthToken": aaas.auth_token.get_secret_value(),
            "stdRqtSyt": aaas.request_system,
            "stdRspSys": aaas.response_system,
            "stdTransId": transaction_id,
            "stdApplySystTmtp": now.strftime("%Y-%m-%d-%H.%M.%S.%f"),
            "stdBankNum": aaas.bank_number,
        }
    )
    return headers


__all__ = ["bank_request_headers"]
