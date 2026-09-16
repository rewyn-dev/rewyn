from __future__ import annotations

from rewyn.models.local import DEFAULT_LOCAL_BASE_URL, LocalModel
from rewyn.models.openai_compatible import OpenAICompatibleModel


def test_compatible_model_sets_base_url_and_label() -> None:
    model = OpenAICompatibleModel("llama", base_url="http://gpu:8000/v1", provider_name="vllm")
    assert model.provider == "vllm"
    assert model._client_kwargs == {"api_key": "not-needed", "base_url": "http://gpu:8000/v1"}
    assert model.dependency.name == "vllm:llama"


def test_local_model_defaults_to_ollama() -> None:
    model = LocalModel("llama3")
    assert model.provider == "local"
    assert model._client_kwargs["base_url"] == DEFAULT_LOCAL_BASE_URL


def test_local_model_base_url_from_env(monkeypatch: object) -> None:
    import os

    os.environ["REWYN_LOCAL_BASE_URL"] = "http://box:1234/v1"
    try:
        assert LocalModel("m")._client_kwargs["base_url"] == "http://box:1234/v1"
    finally:
        del os.environ["REWYN_LOCAL_BASE_URL"]
