"""Configuration for the SDD context module.

Everything is read from the environment so the module can run unchanged on a
reviewer's machine. The defaults point at a local PowerContext and a local
seekdb, which is what ``scripts/run_all.sh`` sets up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = ["Settings", "load_settings"]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime configuration."""

    # --- PowerContext ---
    powercontext_url: str = field(
        default_factory=lambda: _env("POWERCONTEXT_URL", "http://127.0.0.1:8000")
    )
    powercontext_scope_id: str = field(
        default_factory=lambda: _env("POWERCONTEXT_SCOPE_ID")
    )
    powercontext_token: str = field(
        default_factory=lambda: _env("POWERCONTEXT_TOKEN")
    )
    powercontext_timeout: int = field(
        default_factory=lambda: _env_int("POWERCONTEXT_TIMEOUT", 30)
    )

    # --- seekdb / OceanBase ---
    database_host: str = field(default_factory=lambda: _env("SEEKDB_HOST", "127.0.0.1"))
    database_port: int = field(default_factory=lambda: _env_int("SEEKDB_PORT", 2881))
    database_user: str = field(default_factory=lambda: _env("SEEKDB_USER", "root"))
    database_password: str = field(default_factory=lambda: _env("SEEKDB_PASSWORD", ""))
    database_name: str = field(default_factory=lambda: _env("SEEKDB_DATABASE", "sdd_context"))
    database_timeout: int = field(default_factory=lambda: _env_int("SEEKDB_TIMEOUT", 30))

    # --- MiniMax ---
    minimax_api_key: str = field(default_factory=lambda: _env("MINIMAX_API_KEY"))
    minimax_base_url: str = field(
        default_factory=lambda: _env("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
    )
    minimax_model: str = field(
        default_factory=lambda: _env("MINIMAX_MODEL", "MiniMax-M3.1-Flash")
    )
    minimax_effort: str = field(default_factory=lambda: _env("MINIMAX_EFFORT", "medium"))
    minimax_timeout: int = field(default_factory=lambda: _env_int("MINIMAX_TIMEOUT", 300))

    @property
    def powercontext_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.powercontext_token:
            headers["Authorization"] = f"Bearer {self.powercontext_token}"
        return headers


def load_settings() -> Settings:
    """Read the settings from the environment."""
    return Settings()
