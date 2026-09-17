"""CLI configuration, file listing, transfer grants, and upload orchestration.

The CLI uses shared storage and grant services without a LinkParcel server or token.
Each storage command owns its operator on the current event loop; uploads stream files
through httpx2 in a worker thread. The OpenDAL binding has no close method; the operator
is released with the call. Storage results use single-line JSON; failures use stderr.
"""

import asyncio
import logging
import os
import stat
from pathlib import Path
from typing import Annotated, BinaryIO

import httpx2
import typer
from opendal import AsyncOperator

from linkparcel.config import (
    ConfigurationError,
    StorageSettings,
    load_cli_settings,
    write_cli_config,
)
from linkparcel.models import DownloadGrant, DownloadRequest, FileList, UploadGrant, UploadRequest
from linkparcel.services.files import issue_download_grant, issue_upload_grant, list_files
from linkparcel.services.storage import (
    OPENDAL_ERRORS,
    UnsupportedStorageError,
    create_operator,
    ensure_operator_ready,
)

logger = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)


class UploadError(RuntimeError):
    """A safe upload failure, including the stored key when upload already succeeded."""


async def _create_ready_operator(storage_uri: str) -> AsyncOperator:
    """Create and check an operator on the caller's loop, mapping setup errors safely.

    The caller owns the result. Raises ConfigurationError for unsupported capabilities
    or failed backend initialization, without exposing the storage URI or credentials.
    """
    try:
        operator = create_operator(storage_uri)
        await ensure_operator_ready(operator)
    except UnsupportedStorageError as error:
        raise ConfigurationError(str(error)) from None
    except Exception:
        # OpenDAL construction can echo credentials, including in ValueError.
        raise ConfigurationError("Storage backend initialization failed") from None
    return operator


def _upload_to_storage(grant: UploadGrant, file: BinaryIO, content_length: int) -> None:
    """Stream a caller-owned file using signed request components and its exact size.

    Own and close the HTTP client in this worker. Raises UploadError on invalid URLs,
    transport errors, or non-2xx responses; file read errors propagate as OSError.
    """
    headers = httpx2.Headers(grant.headers)
    headers["Content-Length"] = str(content_length)
    try:
        with httpx2.Client() as client:
            response = client.request(grant.method, grant.upload_url, headers=headers, content=file)
    except (httpx2.RequestError, httpx2.InvalidURL) as error:
        logger.debug("Storage request failed: %s", type(error).__name__)
        raise UploadError("Upstream upload failed (network error)") from None
    if not response.is_success:
        raise UploadError(f"Upstream upload failed (HTTP {response.status_code})")


async def upload_file(settings: StorageSettings, path: Path) -> DownloadGrant:
    """Upload one regular file and return its final key and temporary download URL.

    Reject invalid local paths before storage I/O, then check a fresh operator, choose
    a key, stream the file, and presign a read for that key. Naming uses a list snapshot
    without reservations; neither storage requests nor partial uploads are retried.
    File and HTTP client handles close after use. Raises only safe UploadError messages
    for expected local, backend, native argument-conversion, and transport failures.
    A download-signing failure after successful transfer reports the already-stored key.
    """
    try:
        if not path.is_file():
            raise UploadError(f"Not a readable regular file: {path}")
        with path.open("rb") as file:
            file_stat = os.fstat(file.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise UploadError(f"Not a regular file: {path}")
            try:
                operator = await _create_ready_operator(settings.storage_uri)
            except ConfigurationError as error:
                raise UploadError(str(error)) from None
            try:
                grant = await issue_upload_grant(
                    operator,
                    UploadRequest(name=path.name),
                    ttl_seconds=settings.upload_url_ttl_seconds,
                )
            except ValueError as error:
                raise UploadError(str(error)) from None
            except (*OPENDAL_ERRORS, OverflowError):
                raise UploadError("Unable to issue upload URL") from None
            await asyncio.to_thread(_upload_to_storage, grant, file, file_stat.st_size)
    except OSError:
        raise UploadError(f"Unable to read local file: {path}") from None

    try:
        return await issue_download_grant(
            operator,
            DownloadRequest(key=grant.key),
            ttl_seconds=settings.download_url_ttl_seconds,
        )
    except (*OPENDAL_ERRORS, OverflowError):
        raise UploadError(
            f"Object uploaded as `{grant.key}`, but download URL issuance failed"
        ) from None


async def _list_files(settings: StorageSettings) -> FileList:
    """List files with a fresh operator; propagate safe setup and OpenDAL errors."""
    operator = await _create_ready_operator(settings.storage_uri)
    return await list_files(operator)


async def _download_url(settings: StorageSettings, key: str) -> DownloadGrant:
    """Sign an exact key without fetching it; propagate setup, key, and signing errors."""
    operator = await _create_ready_operator(settings.storage_uri)
    return await issue_download_grant(
        operator, DownloadRequest(key=key), ttl_seconds=settings.download_url_ttl_seconds
    )


@app.callback()
def main() -> None:
    """Upload, list, and share files directly from private storage with LinkParcel."""


@app.command()
def config(
    storage_uri: Annotated[str, typer.Option("--storage-uri", help="OpenDAL storage URI")],
    upload_url_ttl_seconds: Annotated[
        int, typer.Option(help="Upload URL lifetime in seconds")
    ] = 300,
    download_url_ttl_seconds: Annotated[
        int, typer.Option(help="Download URL lifetime in seconds")
    ] = 3600,
) -> None:
    """Save local storage configuration and emit only its file path."""
    try:
        path = write_cli_config(
            storage_uri,
            upload_url_ttl_seconds=upload_url_ttl_seconds,
            download_url_ttl_seconds=download_url_ttl_seconds,
        )
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(str(path))


@app.command()
def upload(path: Annotated[Path, typer.Argument(help="Local file to upload")]) -> None:
    """Run the CLI upload workflow and emit one JSON key/download URL record.

    Configuration and upload errors produce a nonzero exit code with empty stdout.
    A failure after the upload identifies the stored key in its stderr message.
    """
    try:
        download = asyncio.run(upload_file(load_cli_settings(), path))
    except (ConfigurationError, UploadError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(download.model_dump_json())


@app.command("list")
def list_command() -> None:
    """Emit all direct file keys in ascending order as one JSON files record.

    An empty store returns {"files": []}. Configuration and storage failures leave
    stdout empty, report a safe message on stderr, and exit with a nonzero status.
    """
    try:
        files = asyncio.run(_list_files(load_cli_settings()))
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    except OPENDAL_ERRORS:
        typer.echo("Unable to list files", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(files.model_dump_json())


@app.command()
def download_url(key: Annotated[str, typer.Argument(help="Exact stored file key")]) -> None:
    """Emit one JSON key/download URL record without downloading or checking the object.

    Keys must encode as UTF-8. Uses the configured download URL lifetime. Configuration,
    key, and signing errors leave stdout empty, report a safe message on stderr, and exit
    with a nonzero status.
    """
    try:
        # Unix argv surrogate escapes cannot be represented in the JSON result.
        key.encode("utf-8")
        download = asyncio.run(_download_url(load_cli_settings(), key))
    except UnicodeEncodeError:
        typer.echo("File key must be valid UTF-8", err=True)
        raise typer.Exit(code=1) from None
    except (ConfigurationError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    except (*OPENDAL_ERRORS, OverflowError):
        typer.echo("Unable to issue download URL", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(download.model_dump_json())
