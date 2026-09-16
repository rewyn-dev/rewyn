"""Adapter for OpenAI-compatible endpoints (vLLM, Groq, Together, LM Studio…)."""

from __future__ import annotations

from typing import Any, ClassVar

from rewyn.models.openai import OpenAIModel


class OpenAICompatibleModel(OpenAIModel):
    provider: ClassVar[str] = "openai_compatible"

    def __init__(
        self,
        name: str,
        *,
        base_url: str,
        api_key: str | None = "not-needed",
        provider_name: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(name, base_url=base_url, api_key=api_key, **kwargs)
        if provider_name:
            self.provider = provider_name  # type: ignore[misc]  # per-instance provider label
