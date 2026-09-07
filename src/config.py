from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM Settings
    llm_provider: str = "gemini"
    llm_model: str = "gemini-1.5-flash"
    
    # Matches GEMINI_API_KEY in .env
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")

    # Web Search Settings
    web_search_provider: str = "tavily"
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")

    # SE Layer Operational Settings
    log_level: str = "INFO"
    cache_dir: str = "./.cache"
    cache_ttl_seconds: int = 86400
    per_source_timeout_seconds: float = 10.0
    max_sources_per_query: int = 3

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()