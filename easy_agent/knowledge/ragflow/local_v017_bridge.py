"""Detachable development bridge from the bank API to local RAGFlow v0.17.

Deleting this file and the single factory hook in ``client.py`` leaves the
bank production contract intact.  It must never be enabled in-bank.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..config import LocalV017BridgeConfig


class LocalV017Bridge:
    def __init__(self, config: LocalV017BridgeConfig):
        self.config = config

    def url(self, operation_name: str, operation_path: str) -> str:
        path = self.config.operation_paths.get(operation_name, operation_path)
        return (
            self.config.base_url.rstrip("/")
            + self.config.api_prefix
            + path
        )

    def headers(self, headers: dict[str, str]) -> dict[str, str]:
        stripped = {name.lower() for name in self.config.strip_headers}
        return {
            name: value
            for name, value in headers.items()
            if name.lower() not in stripped
        }

    def should_skip_document_configuration(self, filename: str) -> bool:
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return extension in set(
            self.config.skip_document_configuration_extensions
        )

    def json_body(
        self, operation_name: str, body: dict[str, object]
    ) -> dict[str, object]:
        adapted = deepcopy(body)
        if (
            operation_name == "create_dataset"
            and self.config.omit_embedding_model_on_create
        ):
            adapted.pop("embedding_model", None)
        if operation_name in {"create_dataset", "update_document"}:
            parser = adapted.get("parser_config")
            if isinstance(parser, dict):
                if isinstance(parser.get("layout_recognize"), bool):
                    parser["layout_recognize"] = self.config.layout_recognize_value
                for field in self.config.strip_parser_config_fields:
                    parser.pop(field, None)
        return adapted


def build_local_v017_bridge(
    config: LocalV017BridgeConfig,
) -> LocalV017Bridge | None:
    return LocalV017Bridge(config) if config.enabled else None


__all__ = ["LocalV017Bridge", "build_local_v017_bridge"]
