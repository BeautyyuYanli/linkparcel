"""Settings contracts that must hold before any network or storage operation."""

import pytest
from pydantic import SecretStr, ValidationError

from oh_my_pastebin.config import Settings


def test_settings_accept_opaque_storage_uri_and_whitespace_token() -> None:
    settings = Settings(
        storage_uri="anything the backend accepts",
        api_token=SecretStr(" "),
    )

    assert settings.storage_uri == "anything the backend accepts"
    assert settings.api_token.get_secret_value() == " "
    assert settings.upload_url_ttl_seconds == 300
    assert settings.download_url_ttl_seconds == 3600


def test_settings_reject_empty_api_token() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        Settings(storage_uri="memory://", api_token=SecretStr(""))


@pytest.mark.parametrize(
    "ttl_field",
    ["upload_url_ttl_seconds", "download_url_ttl_seconds"],
)
def test_settings_reject_each_non_positive_ttl(ttl_field: str) -> None:
    values: dict[str, object] = {
        "storage_uri": "memory://",
        "api_token": SecretStr("secret"),
        ttl_field: 0,
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(values)
