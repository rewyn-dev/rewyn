"""Unified model abstraction and provider adapters.

Provider adapters are imported lazily so ``rewyn.models`` never imports a
provider SDK until you construct a model for that provider::

    from rewyn import models

    model = models.Anthropic("claude-opus-5")
    model = models.OpenAI("gpt-5")
    model = models.Gemini("gemini-2.5-pro")
    model = models.from_string("anthropic:claude-opus-5")
"""

from __future__ import annotations

import importlib
from typing import Any

from rewyn.models.base import (
    Cost,
    ImagePart,
    Message,
    Model,
    ModelError,
    ModelRequest,
    ModelResponse,
    ReasoningPart,
    Role,
    StreamEvent,
    StructuredOutputError,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    coerce_messages,
)
from rewyn.models.pricing import (
    Price,
    UnitPrice,
    register_price,
    register_unit_price,
)
from rewyn.models.registry import register_provider, resolve_model

from_string = resolve_model

_LAZY: dict[str, tuple[str, str]] = {
    "OpenAI": ("rewyn.models.openai", "OpenAIModel"),
    "OpenAIModel": ("rewyn.models.openai", "OpenAIModel"),
    "Anthropic": ("rewyn.models.anthropic", "AnthropicModel"),
    "AnthropicModel": ("rewyn.models.anthropic", "AnthropicModel"),
    "Gemini": ("rewyn.models.google", "GeminiModel"),
    "GeminiModel": ("rewyn.models.google", "GeminiModel"),
    "OpenAICompatible": ("rewyn.models.openai_compatible", "OpenAICompatibleModel"),
    "OpenAICompatibleModel": ("rewyn.models.openai_compatible", "OpenAICompatibleModel"),
    "Local": ("rewyn.models.local", "LocalModel"),
    "LocalModel": ("rewyn.models.local", "LocalModel"),
}

__all__ = [
    "Anthropic",
    "AnthropicModel",
    "Cost",
    "Gemini",
    "GeminiModel",
    "ImagePart",
    "Local",
    "LocalModel",
    "Message",
    "Model",
    "ModelError",
    "ModelRequest",
    "ModelResponse",
    "OpenAI",
    "OpenAICompatible",
    "OpenAICompatibleModel",
    "OpenAIModel",
    "Price",
    "ReasoningPart",
    "Role",
    "StreamEvent",
    "StructuredOutputError",
    "TextPart",
    "ToolCallPart",
    "ToolResultPart",
    "ToolSpec",
    "UnitPrice",
    "Usage",
    "coerce_messages",
    "from_string",
    "register_price",
    "register_provider",
    "register_unit_price",
    "resolve_model",
]


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        module_name, attr = _LAZY[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module 'rewyn.models' has no attribute {name!r}")
