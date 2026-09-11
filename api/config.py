"""Configuración de Byte. Todo se lee del entorno (o de .env en desarrollo)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Entorno ---
    env: Literal["dev", "prod"] = Field(default="dev", alias="BYTE_ENV")
    version: str = Field(default="0.1.0", alias="BYTE_VERSION")

    # --- Credenciales ---
    # La API key viaja en X-API-Key (CLI) o se canjea por una cookie httpOnly (web).
    # Nunca se guarda en claro: api.security la hashea al arrancar.
    api_key: str = Field(default="", alias="BYTE_API_KEY")
    # Firma de la cookie de sesión y de los events_token de 60 s.
    secret_key: str = Field(default="", alias="BYTE_SECRET_KEY")

    # --- Modelo ---
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5-coder:7b", alias="OLLAMA_MODEL")
    # Ollama arranca en 4.096 tokens por defecto aunque el modelo soporte más: se setea explícito.
    ollama_num_ctx: int = Field(default=16384, alias="OLLAMA_NUM_CTX")
    # Tope de tokens por respuesta (denegación de billetera).
    ollama_num_predict: int = Field(default=1024, alias="OLLAMA_NUM_PREDICT")

    # --- Persistencia ---
    database_url: str = Field(default="", alias="DATABASE_URL")
    storage: Literal["auto", "memory", "postgres"] = Field(default="auto", alias="BYTE_STORAGE")

    # --- Herramientas ---
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")
    sandbox_url: str = Field(default="", alias="SANDBOX_URL")

    # --- Límites del agente (seguridad: costos y loops) ---
    max_iterations: int = Field(default=6, alias="BYTE_MAX_ITERATIONS")
    run_timeout_s: int = Field(default=180, alias="BYTE_RUN_TIMEOUT_S")
    max_message_chars: int = Field(default=8000, alias="BYTE_MAX_MESSAGE_CHARS")
    max_concurrent_runs: int = Field(default=2, alias="BYTE_MAX_CONCURRENT_RUNS")
    # Tamaño máximo de cada resultado de herramienta inyectado en el prompt.
    max_tool_result_chars: int = Field(default=4000, alias="BYTE_MAX_TOOL_RESULT_CHARS")
    max_search_query_chars: int = Field(default=200, alias="BYTE_MAX_SEARCH_QUERY_CHARS")

    # --- Rate limits (por credencial) ---
    rate_limit_general: str = Field(default="60/minute", alias="BYTE_RATE_LIMIT_GENERAL")
    rate_limit_runs: str = Field(default="10/minute", alias="BYTE_RATE_LIMIT_RUNS")

    # --- Web ---
    cors_origins: list[str] = Field(default_factory=list, alias="BYTE_CORS_ORIGINS")
    # Los events_token duran 60 s y son de un solo uso (contrato de la API).
    events_token_ttl_s: int = Field(default=60, alias="BYTE_EVENTS_TOKEN_TTL_S")
    session_ttl_s: int = Field(default=86400, alias="BYTE_SESSION_TTL_S")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Acepta "http://a,http://b" desde el entorno."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def use_postgres(self) -> bool:
        if self.storage == "postgres":
            return True
        if self.storage == "memory":
            return False
        return bool(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
