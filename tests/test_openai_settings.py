from __future__ import annotations

from news_agent.openai_settings import DEFAULT_OPENAI_MODEL, resolve_openai_model


def test_resolve_openai_model_uses_shared_default_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert resolve_openai_model() == DEFAULT_OPENAI_MODEL


def test_resolve_openai_model_prefers_explicit_override_then_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")

    assert resolve_openai_model() == "environment-model"
    assert resolve_openai_model("explicit-model") == "explicit-model"
