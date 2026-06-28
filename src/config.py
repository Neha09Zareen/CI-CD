# src/config.py
"""Centralized configuration for the AI CI/CD Agent backend.

All runtime configuration is read from environment variables (loaded from
.env by the app entrypoint). Importing this module never raises when secrets
are absent; features that require a secret validate it at call time and raise
a clear error then. This keeps the app importable in any environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _csv(value: str | None, default: list[str]) -> list[str]:
    """Parse a comma-separated env value into a list, or return the default."""
    if not value:
        return default
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or default


@dataclass(frozen=True)
class Settings:
    """Immutable application settings sourced from the environment."""

    # Secrets (optional at import time; required only when their feature runs)
    groq_api_key: str | None = None
    github_token: str | None = None

    # Model routing (cascadeflow)
    fast_model: str = "llama-3.1-8b-instant"
    reasoning_model: str = "llama-3.3-70b-versatile"

    # Persistence paths
    store_path: str = "failure_store.json"
    hindsight_path: str = "hindsight_db.json"

    # CORS origins for the frontend
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def has_github(self) -> bool:
        return bool(self.github_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings read from the environment."""
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY"),
        github_token=os.getenv("GITHUB_TOKEN"),
        fast_model=os.getenv("FAST_MODEL", "llama-3.1-8b-instant"),
        reasoning_model=os.getenv("REASONING_MODEL", "llama-3.3-70b-versatile"),
        store_path=os.getenv("STORE_PATH", "failure_store.json"),
        hindsight_path=os.getenv("HINDSIGHT_PATH", "hindsight_db.json"),
        cors_origins=_csv(os.getenv("CORS_ORIGINS"), ["*"]),
    )
