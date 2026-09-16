"""Resolve ``"provider:model"`` strings into :class:`Model` instances."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rewyn.core.types import ConfigurationError
from rewyn.models.base import Model

ModelFactory = Callable[[str], Model]

_FACTORIES: dict[str, ModelFactory] = {}


def register_provider(prefix: str, factory: ModelFactory) -> None:
    """Register a factory used for ``"<prefix>:<model>"`` strings."""
    _FACTORIES[prefix] = factory


def _openai(name: str) -> Model:
    from rewyn.models.openai import OpenAIModel

    return OpenAIModel(name)


def _anthropic(name: str) -> Model:
    from rewyn.models.anthropic import AnthropicModel

    return AnthropicModel(name)


def _google(name: str) -> Model:
    from rewyn.models.google import GeminiModel

    return GeminiModel(name)


def _local(name: str) -> Model:
    from rewyn.models.local import LocalModel

    return LocalModel(name)


def _fake(name: str) -> Model:
    from rewyn.testing.fake_model import FakeModel

    return FakeModel(name=name)


for _prefix, _factory in {
    "openai": _openai,
    "anthropic": _anthropic,
    "google": _google,
    "gemini": _google,
    "local": _local,
    "ollama": _local,
    "fake": _fake,
}.items():
    register_provider(_prefix, _factory)


def resolve_model(spec: str | Model, **_: Any) -> Model:
    """Return a model for a ``Model`` instance or a ``"provider:model"`` string."""
    if isinstance(spec, Model):
        return spec
    if ":" not in spec:
        raise ConfigurationError(
            f"model {spec!r} must be given as 'provider:model', e.g. 'anthropic:claude-opus-5'"
        )
    prefix, name = spec.split(":", 1)
    factory = _FACTORIES.get(prefix)
    if factory is None:
        raise ConfigurationError(
            f"unknown model provider {prefix!r}; known providers: {sorted(_FACTORIES)}"
        )
    return factory(name)


def known_providers() -> list[str]:
    return sorted(_FACTORIES)
