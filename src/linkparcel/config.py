"""Shared storage settings, server-only credentials, and private CLI configuration.

The storage URI is opaque: preserve it for OpenDAL and never include it in diagnostics.
Only CLI loading reads the XDG file; server settings read the process environment.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, set_key, unset_key
from pydantic import Field, PositiveInt, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """A configuration failure safe to display without credentials or raw input."""


class StorageSettings(BaseSettings):
    """Storage configuration shared by both controllers, with no application token."""

    model_config = SettingsConfigDict(env_prefix="LPC_", extra="ignore", hide_input_in_errors=True)

    storage_uri: str = Field(min_length=1, repr=False)
    upload_url_ttl_seconds: PositiveInt = 300
    download_url_ttl_seconds: PositiveInt = 3600


class ServerSettings(StorageSettings):
    """Environment-backed storage settings plus the server's shared Bearer token."""

    api_token: SecretStr

    @field_validator("api_token")
    @classmethod
    def reject_empty_api_token(cls, api_token: SecretStr) -> SecretStr:
        """Reject only the empty token; length and entropy policy stays operational."""
        if api_token.get_secret_value() == "":
            raise ValueError("LPC_API_TOKEN must not be empty")
        return api_token


def cli_config_path() -> Path:
    """Return ``${XDG_CONFIG_HOME:-$HOME/.config}/lpc/env``."""
    config_home = os.getenv("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "lpc" / "env"


def load_cli_settings() -> StorageSettings:
    """Load storage settings with each process value overriding the XDG file.

    Dotenv interpolation is disabled so credentials containing ``${...}`` remain
    opaque. Missing optional fields use model defaults.
    Raises ConfigurationError on unreadable or invalid configuration, with setup guidance.
    """
    try:
        stored = dotenv_values(cli_config_path(), interpolate=False)
        values = {
            field: os.environ.get(key, stored.get(key))
            for field in StorageSettings.model_fields
            if (key := f"LPC_{field.upper()}") in os.environ or key in stored
        }
        return StorageSettings.model_validate(values)
    except (OSError, ValueError):
        raise ConfigurationError(
            "Storage configuration is missing or invalid; run lpc config --storage-uri <URI>"
        ) from None


def write_cli_config(
    storage_uri: str,
    *,
    upload_url_ttl_seconds: int = 300,
    download_url_ttl_seconds: int = 3600,
) -> Path:
    """Validate explicit arguments and save all storage settings without backend I/O.

    The directory and file have modes 0700 and 0600. Symlink or non-regular file
    targets are rejected. Only storage settings are updated; unrelated keys are retained.
    Values use python-dotenv's replacement and quoting, with literal backslashes
    escaped and the opaque URI kept last to avoid its trailing-escape parser ambiguity.
    Raises ConfigurationError on invalid input or an unsafe/unwritable target.
    """
    try:
        settings = StorageSettings(
            storage_uri=storage_uri,
            upload_url_ttl_seconds=upload_url_ttl_seconds,
            download_url_ttl_seconds=download_url_ttl_seconds,
        )
    except ValidationError:
        raise ConfigurationError("Storage configuration is invalid") from None

    path = cli_config_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ConfigurationError("CLI config path is not a regular file")
        if path.exists():
            path.chmod(0o600)
        stored = dotenv_values(path, interpolate=False)
        # python-dotenv escapes quotes but not backslashes. Its quoted-value parser
        # also treats a trailing backslash as escaping the closing quote when another
        # quoted setting follows, so move the existing URI after the other keys.
        if "LPC_STORAGE_URI" in stored:
            unset_key(path, "LPC_STORAGE_URI")
        values = {
            "LPC_UPLOAD_URL_TTL_SECONDS": str(settings.upload_url_ttl_seconds),
            "LPC_DOWNLOAD_URL_TTL_SECONDS": str(settings.download_url_ttl_seconds),
            "LPC_STORAGE_URI": settings.storage_uri,
        }
        for key, value in values.items():
            set_key(path, key, value.replace("\\", "\\\\"))
            path.chmod(0o600)
    except (OSError, UnicodeError):
        raise ConfigurationError("Unable to write CLI config") from None
    return path
