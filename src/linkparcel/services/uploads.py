"""Upload local files directly through the shared storage and grant services.

Each call owns its operator on the current event loop and streams the file through
httpx2 in a worker thread. No LinkParcel API or application token participates.
The OpenDAL Python binding has no close method; its operator is released with the call.
"""

import asyncio
import logging
import os
import stat
from pathlib import Path
from typing import BinaryIO

import httpx2

from linkparcel.config import StorageSettings
from linkparcel.models import DownloadGrant, DownloadRequest, UploadGrant, UploadRequest
from linkparcel.services.files import issue_download_grant, issue_upload_grant
from linkparcel.services.storage import (
    OPENDAL_ERRORS,
    UnsupportedStorageError,
    create_operator,
    ensure_operator_ready,
)

logger = logging.getLogger(__name__)


class UploadError(RuntimeError):
    """A safe upload failure, including the stored key when upload already succeeded."""


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
    A download-signing failure
    after successful transfer reports the already-stored key.
    """
    try:
        if not path.is_file():
            raise UploadError(f"Not a readable regular file: {path}")
        with path.open("rb") as file:
            file_stat = os.fstat(file.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise UploadError(f"Not a regular file: {path}")
            try:
                operator = create_operator(settings.storage_uri)
                await ensure_operator_ready(operator)
            except UnsupportedStorageError as error:
                raise UploadError(str(error)) from None
            except Exception:
                # OpenDAL construction can echo credentials, including in ValueError.
                raise UploadError("Storage backend initialization failed") from None
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
