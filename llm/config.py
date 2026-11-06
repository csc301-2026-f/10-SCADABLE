"""Downstream analysis settings, separate from the scraper's collection policy."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from scraper.environment import default_env_file


class Settings(BaseSettings):
    """Read process overrides and the shared repository-root .env."""

    model_config = SettingsConfigDict(
        env_file=default_env_file(), env_file_encoding="utf-8-sig", extra="ignore"
    )

    openai_api_key: SecretStr | None = None
    openai_model: str = ""
    openai_timeout_seconds: float = Field(default=30.0, gt=0)
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str | None = None
