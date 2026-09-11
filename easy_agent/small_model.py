"""Configuration models for embedding, reranking, and other small models.

Large-language-model construction stays in :mod:`easy_agent.model`.  This
module is the corresponding boundary for non-generative models used by
knowledge retrieval.  Runtime values still come from the active EasyAgent YAML
(``config.dev.yaml`` in development); no endpoint or model name is hardcoded in
the knowledge implementation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SmallModelConfig(BaseModel):
    """One externally hosted small model."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "ragflow"
    model: str = ""
    api_base: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SmallModelsConfig(BaseModel):
    """Small-model registry loaded from the active application config."""

    model_config = ConfigDict(extra="forbid")

    embedding: SmallModelConfig = Field(default_factory=SmallModelConfig)
    reranker: SmallModelConfig = Field(default_factory=SmallModelConfig)

    @classmethod
    def from_mapping(cls, value: object) -> "SmallModelsConfig":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("small_models must be a YAML mapping")
        return cls.model_validate(value)
