"""CLI configuration and storage command contracts without external services."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from dotenv import dotenv_values
from opendal import AsyncOperator, exceptions
from opendal.types import Entry
from typer.testing import CliRunner

from linkparcel.config import StorageSettings, load_cli_settings
from linkparcel.controllers import cli
from linkparcel.controllers.cli import UploadError
from linkparcel.models import DownloadGrant

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


def test_upload_runs_workflow_and_emits_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque-uri")
    workflow = AsyncMock(
        return_value=DownloadGrant(key="report-1.pdf", download_url="https://s/read")
    )
    monkeypatch.setattr(cli, "upload_file", workflow)
    path = tmp_path / "report.pdf"

    result = runner.invoke(cli.app, ["upload", str(path)])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"key": "report-1.pdf", "download_url": "https://s/read"}
    assert len(result.stdout.splitlines()) == 1
    workflow.assert_awaited_once_with(StorageSettings(storage_uri="opaque-uri"), path)


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


@pytest.mark.parametrize(
    "args", [["upload", "report.pdf"], ["list"], ["download-url", "report.pdf"]]
)
def test_storage_commands_require_storage_setup(config_home: Path, args: list[str]) -> None:
    path = config_home / "lpc" / "env"
    path.parent.mkdir(parents=True)
    path.write_text("LPC_API_URL=https://old.test\nLPC_API_TOKEN=old\n")

    result = runner.invoke(cli.app, args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "run lpc config --storage-uri <URI>" in result.stderr


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Provide the OpenDAL async boundary without real storage requests."""
    fake = Mock(spec=AsyncOperator)
    fake.capability.return_value = SimpleNamespace(list=True, presign_write=True, presign_read=True)
    fake.check = AsyncMock()
    fake.presign_read = AsyncMock(return_value=SimpleNamespace(url="https://storage.test/download"))
    monkeypatch.setattr(cli, "create_operator", Mock(return_value=fake))
    return fake


@pytest.mark.parametrize("keys", [[], ["report.pdf"]])
def test_list_emits_file_list_json(
    monkeypatch: pytest.MonkeyPatch, operator: Mock, keys: list[str]
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque-uri")

    async def entries(_path: str) -> AsyncIterator[Entry]:
        for key in keys:
            yield cast(Entry, SimpleNamespace(path=key, metadata=SimpleNamespace(is_file=True)))

    operator.list = AsyncMock(side_effect=entries)

    result = runner.invoke(cli.app, ["list"])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"files": [{"key": key} for key in keys]}
    assert len(result.stdout.splitlines()) == 1
    assert result.stderr == ""
    cast(Mock, cli.create_operator).assert_called_once_with("opaque-uri")
    operator.list.assert_awaited_once_with("")


@pytest.mark.parametrize("override_ttl", [None, 900])
def test_download_url_uses_configured_ttl_and_exact_key_without_fetching_object(
    config_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator: Mock,
    override_ttl: int | None,
) -> None:
    path = config_home / "lpc" / "env"
    path.parent.mkdir(parents=True)
    path.write_text("LPC_STORAGE_URI=opaque-uri\nLPC_DOWNLOAD_URL_TTL_SECONDS=600\n")
    if override_ttl is not None:
        monkeypatch.setenv("LPC_DOWNLOAD_URL_TTL_SECONDS", str(override_ttl))
    key = " 报告-1.pdf "

    result = runner.invoke(cli.app, ["download-url", key])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {
        "key": key,
        "download_url": "https://storage.test/download",
    }
    assert len(result.stdout.splitlines()) == 1
    assert result.stderr == ""
    cast(Mock, cli.create_operator).assert_called_once_with("opaque-uri")
    operator.presign_read.assert_awaited_once_with(key, override_ttl or 600)
    operator.list.assert_not_called()
    operator.stat.assert_not_called()
    operator.read.assert_not_called()


@pytest.mark.parametrize("args", [["list"], ["download-url", "report.pdf"]])
@pytest.mark.parametrize("failure", ["construct", "check", "capability"])
def test_read_commands_report_safe_initialization_errors(
    monkeypatch: pytest.MonkeyPatch, operator: Mock, args: list[str], failure: str
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "private-uri")
    message = "Storage backend initialization failed"
    if failure == "construct":
        monkeypatch.setattr(cli, "create_operator", Mock(side_effect=ValueError("private-uri")))
    elif failure == "check":
        operator.check.side_effect = exceptions.PermissionDenied("private-uri")
    else:
        operator.capability.return_value.presign_read = False
        message = "Storage backend lacks required capabilities: presign_read"

    result = runner.invoke(cli.app, args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"{message}\n"


@pytest.mark.parametrize(
    ("args", "operation", "failure", "message"),
    [
        (["list"], "list", exceptions.PermissionDenied("private-uri"), "Unable to list files"),
        (
            ["download-url", "report.pdf"],
            "presign_read",
            exceptions.PermissionDenied("private-uri"),
            "Unable to issue download URL",
        ),
        (
            ["download-url", "report.pdf"],
            "presign_read",
            OverflowError("private-uri"),
            "Unable to issue download URL",
        ),
    ],
)
def test_read_commands_report_safe_operation_errors(
    monkeypatch: pytest.MonkeyPatch,
    operator: Mock,
    args: list[str],
    operation: str,
    failure: Exception,
    message: str,
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "private-uri")
    setattr(operator, operation, AsyncMock(side_effect=failure))

    result = runner.invoke(cli.app, args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"{message}\n"


def test_download_url_reports_invalid_key_on_stderr(
    monkeypatch: pytest.MonkeyPatch, operator: Mock
) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque-uri")

    result = runner.invoke(cli.app, ["download-url", "../report.pdf"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "File name must be a single path segment\n"
    operator.presign_read.assert_not_awaited()


def test_download_url_rejects_non_utf8_key(monkeypatch: pytest.MonkeyPatch, operator: Mock) -> None:
    monkeypatch.setenv("LPC_STORAGE_URI", "opaque-uri")
    # Unix argv uses surrogate escapes for bytes that cannot be decoded as UTF-8.
    result = runner.invoke(cli.app, ["download-url", "\udcff"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "File key must be valid UTF-8\n"
