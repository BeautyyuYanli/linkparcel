"""Settings contracts that must hold before any network or storage operation."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from linkparcel.config import ServerSettings, load_cli_settings, write_cli_config


def test_settings_accept_opaque_storage_uri_and_whitespace_token() -> None:
    settings = ServerSettings(
        storage_uri="anything the backend accepts",
        api_token=SecretStr(" "),
    )

    assert settings.storage_uri == "anything the backend accepts"
    assert settings.api_token.get_secret_value() == " "
    assert settings.upload_url_ttl_seconds == 300
    assert settings.download_url_ttl_seconds == 3600


def test_settings_reject_empty_api_token() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        ServerSettings(storage_uri="memory://", api_token=SecretStr(""))


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
        ServerSettings.model_validate(values)


def test_cli_settings_override_individual_file_values_and_preserve_opaque_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("LPC_STORAGE_URI", raising=False)
    monkeypatch.delenv("LPC_DOWNLOAD_URL_TTL_SECONDS", raising=False)
    uri = "s3://bucket?secret_access_key=literal-${KEEP_THIS}'quoted&percent=%25"
    write_cli_config(uri, upload_url_ttl_seconds=200, download_url_ttl_seconds=800)
    monkeypatch.setenv("KEEP_THIS", "must-not-be-interpolated")
    monkeypatch.setenv("LPC_UPLOAD_URL_TTL_SECONDS", "400")
    monkeypatch.setenv("LPC_API_TOKEN", "")

    settings = load_cli_settings()

    assert settings.storage_uri == uri
    assert settings.upload_url_ttl_seconds == 400
    assert settings.download_url_ttl_seconds == 800


def test_cli_settings_work_with_only_environment_and_default_ttls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque")
    monkeypatch.delenv("LPC_UPLOAD_URL_TTL_SECONDS", raising=False)
    monkeypatch.delenv("LPC_DOWNLOAD_URL_TTL_SECONDS", raising=False)
    monkeypatch.delenv("LPC_API_TOKEN", raising=False)

    settings = load_cli_settings()

    assert settings.storage_uri == "opaque"
    assert settings.upload_url_ttl_seconds == 300
    assert settings.download_url_ttl_seconds == 3600


def test_server_ignores_xdg_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("LPC_STORAGE_URI", raising=False)
    monkeypatch.setenv("LPC_API_TOKEN", "server-only")
    write_cli_config("cli-only-storage")

    with pytest.raises(ValidationError, match="storage_uri"):
        ServerSettings()  # pyright: ignore[reportCallIssue]


def test_server_requires_application_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque")
    monkeypatch.delenv("LPC_API_TOKEN", raising=False)

    with pytest.raises(ValidationError, match="api_token"):
        ServerSettings()  # pyright: ignore[reportCallIssue]
