"""Local models served through an OpenAI-compatible API (Ollama, llama.cpp, vLLM)."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from rewyn.models.openai_compatible import OpenAICompatibleModel

DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"


class LocalModel(OpenAICompatibleModel):
    provider: ClassVar[str] = "local"

    def __init__(self, name: str, *, base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(
            name,
            base_url=base_url or os.environ.get("REWYN_LOCAL_BASE_URL", DEFAULT_LOCAL_BASE_URL),
            **kwargs,
        )
