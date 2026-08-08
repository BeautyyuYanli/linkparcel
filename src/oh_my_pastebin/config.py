"""Environment-backed server configuration.

The storage URI is opaque and must only be passed to OpenDAL. In particular, this
module does not parse it or expose it in validation errors beyond normal field presence.
"""

from pydantic import PositiveInt, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded once during the FastAPI application lifespan."""

    model_config = SettingsConfigDict(env_prefix="OMP_", extra="ignore")

    storage_uri: str
    api_token: SecretStr
    upload_url_ttl_seconds: PositiveInt = 300
    download_url_ttl_seconds: PositiveInt = 3600

    @field_validator("api_token")
    @classmethod
    def reject_empty_api_token(cls, api_token: SecretStr) -> SecretStr:
        """Reject only the empty token; policy such as length or entropy stays operational."""
        if api_token.get_secret_value() == "":
            raise ValueError("OMP_API_TOKEN must not be empty")
        return api_token
