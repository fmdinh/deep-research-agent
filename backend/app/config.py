"""Centralized settings, loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    tavily_api_key: str = ""

    # Model used for planning / evaluating / synthesizing. Override in .env if needed.
    claude_model: str = "claude-sonnet-5"

    max_iterations_hard_cap: int = 6
    request_timeout_seconds: int = 60

    cors_origins: list[str] = ["*"]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
