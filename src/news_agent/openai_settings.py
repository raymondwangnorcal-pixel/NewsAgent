from __future__ import annotations

import os


DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


def resolve_openai_model(model: str | None = None) -> str:
    """Return an explicit model override, then the environment setting, then the shared default."""

    return model or os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
