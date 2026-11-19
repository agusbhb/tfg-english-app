from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    mongodb_uri: SecretStr
    mongodb_db: str = Field(default="trainer", pattern=r"^[A-Za-z0-9_-]+$")
    jwt_secret: SecretStr
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    bank_refill_threshold: int = Field(default=20, ge=0, le=10000)
    bank_refill_enabled: bool = False
    openrouter_api_key: SecretStr | None = None
    openrouter_model: str = Field(
        default="anthropic/claude-haiku-4.5", pattern=r"^anthropic/[a-z0-9][a-z0-9._-]+$"
    )
    openrouter_max_completion_tokens: int = Field(default=4096, ge=256, le=16000)
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = Field(
        default="claude-haiku-4-5-20251001", pattern=r"^claude-[a-z0-9][a-z0-9._-]+$"
    )
    anthropic_max_tokens: int = Field(default=4096, ge=256, le=16000)

    bank_refill_batch_size: int = Field(default=30, ge=1, le=50)
    bank_generation_max_rounds: int = Field(default=3, ge=1, le=5)
    bank_refill_poll_seconds: float = Field(default=1, ge=0.01, le=30)
    bank_refill_timeout_seconds: float = Field(default=60, gt=0, le=300)
    bank_refill_lease_seconds: int = Field(default=90, ge=2, le=600)
    bank_refill_retry_seconds: float = Field(default=5, ge=0, le=300)
    bank_refill_max_attempts: int = Field(default=3, ge=1, le=5)

    @field_validator("openrouter_api_key", "anthropic_api_key", mode="before")
    @classmethod
    def optional_openrouter_key(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def refill_lease_covers_timeout(self):
        if self.bank_refill_lease_seconds < self.bank_refill_timeout_seconds + 1:
            raise ValueError("El lease de reposición debe superar el timeout del proveedor")
        return self

    @field_validator("jwt_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32 or any(
            marker in secret.lower()
            for marker in ("change_me", "changeme", "example", "replace_me")
        ):
            raise ValueError(
                "JWT_SECRET debe ser aleatorio, tener al menos 32 caracteres y no ser un marcador"
            )
        return value

    @field_validator("mongodb_uri")
    @classmethod
    def validate_uri(cls, value: SecretStr) -> SecretStr:
        uri = value.get_secret_value()
        if not uri.startswith(("mongodb://", "mongodb+srv://")) or "CHANGE_ME" in uri:
            raise ValueError("Configura MONGODB_URI con una conexión MongoDB válida")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def explicit_origins(cls, values: list[str]) -> list[str]:
        for origin in values:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
            ):
                raise ValueError("ALLOWED_ORIGINS requiere orígenes HTTP explícitos sin rutas")
        return values
