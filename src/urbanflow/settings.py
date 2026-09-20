from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
    )

    database_url: str = Field(
        default="postgresql+asyncpg://urbanflow@db/urbanflow",
        validation_alias="DATABASE_URL",
        min_length=1,
    )
    overpass_url: str = Field(
        default="https://overpass-api.de/api/interpreter",
        validation_alias="OVERPASS_URL",
        min_length=1,
    )
    llm_provider: Literal["openai", "ollama"] = Field(
        default="openai", validation_alias="LLM_PROVIDER"
    )
    llm_base_url: str | None = Field(default=None, validation_alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY", repr=False)
    llm_model: str = Field(default="", validation_alias="LLM_MODEL")
    openai_base_url: str = Field(
        default="http://host.docker.internal:1234/v1",
        validation_alias="OPENAI_BASE_URL",
        min_length=1,
    )
    ollama_base_url: str = Field(
        default="http://host.docker.internal:11434",
        validation_alias="OLLAMA_BASE_URL",
        min_length=1,
    )

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def empty_base_url_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    def endpoint_for(self, provider: Literal["openai", "ollama"]) -> str:
        if provider == self.llm_provider and self.llm_base_url:
            return self.llm_base_url
        return self.openai_base_url if provider == "openai" else self.ollama_base_url
