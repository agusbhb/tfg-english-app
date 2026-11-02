from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    mongodb_uri: SecretStr
    mongodb_db: str = Field(default="trainer", pattern=r"^[A-Za-z0-9_-]+$")
    jwt_secret: SecretStr
    access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

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
