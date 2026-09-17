"""CLI configuration and upload output contracts without external services."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from dotenv import dotenv_values
from typer.testing import CliRunner

from linkparcel.config import StorageSettings, load_cli_settings
from linkparcel.controllers import cli
from linkparcel.models import DownloadGrant
from linkparcel.services.uploads import UploadError

runner = CliRunner()


@pytest.fixture(autouse=True)
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(path))
    for variable in (
        "LPC_STORAGE_URI",
        "LPC_UPLOAD_URL_TTL_SECONDS",
        "LPC_DOWNLOAD_URL_TTL_SECONDS",
        "LPC_API_TOKEN",
        "LPC_API_URL",
    ):
        monkeypatch.delenv(variable, raising=False)
    return path


@pytest.mark.parametrize(
    "uri",
    [
        "s3://bucket?secret_access_key=secret value's&endpoint=https%3A%2F%2Fstorage.test",
        "s3://bucket?secret_access_key=two" + "\\" * 2 + "slashes",
        "s3://bucket?secret_access_key=trailing" + "\\",
    ],
)
def test_config_writes_private_xdg_settings(config_home: Path, uri: str) -> None:
    path = config_home / "lpc" / "env"
    path.parent.mkdir(parents=True)
    path.write_text("LPC_STORAGE_URI='old'\nOTHER='retained'\n")

    result = runner.invoke(cli.app, ["config", "--storage-uri", uri])

    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"{path}\n"
    assert dotenv_values(path, interpolate=False) == {
        "OTHER": "retained",
        "LPC_STORAGE_URI": uri,
        "LPC_UPLOAD_URL_TTL_SECONDS": "300",
        "LPC_DOWNLOAD_URL_TTL_SECONDS": "3600",
    }
    assert load_cli_settings().storage_uri == uri
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_config_replaces_legacy_keys_preserves_unrelated_values_and_ignores_environment(
    config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_home / "lpc" / "env"
    path.parent.mkdir(parents=True)
    path.parent.chmod(0o777)
    path.write_text("LPC_API_URL=https://old.test\nLPC_API_TOKEN=old\nOTHER=retained\n")
    path.chmod(0o666)
    monkeypatch.setenv("LPC_STORAGE_URI", "environment-secret")
    monkeypatch.setenv("LPC_UPLOAD_URL_TTL_SECONDS", "999")
    monkeypatch.setenv("LPC_DOWNLOAD_URL_TTL_SECONDS", "invalid")

    result = runner.invoke(
        cli.app, ["config", "--storage-uri", "opaque-uri", "--download-url-ttl-seconds", "120"]
    )

    assert result.exit_code == 0, result.stderr
    assert dotenv_values(path) == {
        "OTHER": "retained",
        "LPC_STORAGE_URI": "opaque-uri",
        "LPC_UPLOAD_URL_TTL_SECONDS": "300",
        "LPC_DOWNLOAD_URL_TTL_SECONDS": "120",
    }
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_config_defaults_to_home_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(home))

    result = runner.invoke(cli.app, ["config", "--storage-uri", "opaque-uri"])

    path = home / ".config" / "lpc" / "env"
    assert result.exit_code == 0, result.stderr
    assert result.stdout == f"{path}\n"
    assert dotenv_values(path)["LPC_STORAGE_URI"] == "opaque-uri"


@pytest.mark.parametrize(
    "args",
    [
        ["--storage-uri", ""],
        ["--storage-uri", "private-uri", "--upload-url-ttl-seconds", "0"],
        ["--storage-uri", "private-uri", "--download-url-ttl-seconds", "-1"],
    ],
)
def test_config_rejects_invalid_settings_without_exposing_input(args: list[str]) -> None:
    result = runner.invoke(cli.app, ["config", *args])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "Storage configuration is invalid\n"


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_config_rejects_unsafe_target(config_home: Path, tmp_path: Path, kind: str) -> None:
    path = config_home / "lpc" / "env"
    path.parent.mkdir(parents=True)
    target = tmp_path / "untouched"
    if kind == "symlink":
        target.write_text("keep this")
        path.symlink_to(target)
    else:
        path.mkdir()

    result = runner.invoke(cli.app, ["config", "--storage-uri", "opaque-uri"])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert result.stderr == "CLI config path is not a regular file\n"
    if kind == "symlink":
        assert target.read_text() == "keep this"


def test_upload_calls_local_service_and_emits_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque-uri")
    service = AsyncMock(
        return_value=DownloadGrant(key="report-1.pdf", download_url="https://s/read")
    )
    monkeypatch.setattr(cli, "upload_file", service)
    path = tmp_path / "report.pdf"

    result = runner.invoke(cli.app, ["upload", str(path)])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"key": "report-1.pdf", "download_url": "https://s/read"}
    assert len(result.stdout.splitlines()) == 1
    service.assert_awaited_once_with(StorageSettings(storage_uri="opaque-uri"), path)


def test_upload_reports_partial_success_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque-uri")
    message = "Object uploaded as `report-1.pdf`, but download URL issuance failed"
    monkeypatch.setattr(cli, "upload_file", AsyncMock(side_effect=UploadError(message)))

    result = runner.invoke(cli.app, ["upload", str(tmp_path / "report.pdf")])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"{message}\n"


def test_upload_legacy_config_requires_storage_setup(config_home: Path, tmp_path: Path) -> None:
    path = config_home / "lpc" / "env"
    path.parent.mkdir(parents=True)
    path.write_text("LPC_API_URL=https://old.test\nLPC_API_TOKEN=old\n")

    result = runner.invoke(cli.app, ["upload", str(tmp_path / "report.pdf")])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "run lpc config --storage-uri <URI>" in result.stderr
