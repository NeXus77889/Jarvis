"""Environment-backed configuration for JARVIS."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    env_file = ROOT_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value.")


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return parsed


_load_env_file()


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "").strip()
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()
    debug: bool = _bool_env("JARVIS_DEBUG", False)
    memory_enabled: bool = _bool_env("JARVIS_MEMORY_ENABLED", True)
    max_history: int = _int_env("JARVIS_MAX_HISTORY", 12)
    max_memory_results: int = _int_env("JARVIS_MAX_MEMORY_RESULTS", 5)
    max_context_tokens: int = _int_env("JARVIS_MAX_CONTEXT_TOKENS", 6000)
    max_memory_tokens: int = _int_env("JARVIS_MAX_MEMORY_TOKENS", 1200)
    max_response_tokens: int = _int_env("JARVIS_MAX_RESPONSE_TOKENS", 1200)
    log_level: str = os.getenv("JARVIS_LOG_LEVEL", "INFO").upper()
    vosk_model_path: Path | None = (
        Path(os.getenv("JARVIS_VOSK_MODEL_PATH", "")).expanduser()
        if os.getenv("JARVIS_VOSK_MODEL_PATH", "").strip()
        else None
    )
    workspace_path: Path = Path(
        os.getenv("JARVIS_WORKSPACE", str(ROOT_DIR))
    ).expanduser().resolve()
    project_database_path: str | None = (
        os.getenv("JARVIS_PROJECT_DB_PATH", "").strip() or None
    )
    project_database_tables: tuple[str, ...] = tuple(
        table.strip()
        for table in os.getenv("JARVIS_PROJECT_DB_TABLES", "").split(",")
        if table.strip()
    )
    database_path: Path = Path(
        os.getenv("JARVIS_DATABASE_PATH", str(ROOT_DIR / "storage" / "memory" / "jarvis_memory.db"))
    ).expanduser()

    def __post_init__(self) -> None:
        if not self.groq_model:
            raise ValueError("GROQ_MODEL cannot be empty.")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("JARVIS_LOG_LEVEL is not a recognized logging level.")


settings = Settings()
