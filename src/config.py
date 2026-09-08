import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env file."""

    # Provider Options
    llm_provider: str = Field(
        default="gemini",
        description="LLM Provider to use: 'anthropic', 'openai', or 'gemini'",
    )
    llm_model: str = Field(
        default="gemini-2.5-flash",
        description="Model name for the selected LLM provider",
    )
    web_search_provider: str = Field(
        default="tavily",
        description="Web search provider: 'tavily', 'serper', or 'duckduckgo'",
    )

    # API Keys (Optional defaults allow offline/DDG mode)
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    serper_api_key: str | None = Field(default=None, alias="SERPER_API_KEY")

    # Timeouts & Concurrency
    per_source_timeout_seconds: float = Field(
        default=8.0,
        description="Maximum seconds allowed per source fetch before timing out",
    )
    max_retries: int = Field(
        default=3,
        description="Maximum exponential backoff retries for transient HTTP/LLM errors",
    )

    # Cache Settings
    cache_dir: Path = Field(
        default=Path(".cache"),
        description="Directory used for filesystem disk cache storage",
    )
    cache_ttl_seconds: int = Field(
        default=86400,
        description="Cache TTL in seconds (default is 24 hours)",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Application log level: DEBUG, INFO, WARNING, ERROR",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


# Global singleton instance
settings = Settings()
