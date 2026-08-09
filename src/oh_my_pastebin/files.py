"""File naming and OpenDAL operations for the API control plane.

The operator root is the entire pastebin namespace. Upload naming deliberately uses
one list snapshot and makes no reservation or concurrency guarantee; the backend owns
the outcome of races between grant creation and direct upload.
"""

import os
import unicodedata
from itertools import count
from typing import TypedDict
from urllib.parse import quote

from opendal import AsyncOperator

from oh_my_pastebin.models import (
    DownloadGrant,
    DownloadRequest,
    FileList,
    PasteFile,
    UploadGrant,
    UploadRequest,
)

MAX_KEY_CODEPOINTS = 200
DEFAULT_CONTENT_TYPE = "application/octet-stream"


class _PresignWriteOptions(TypedDict, total=False):
    content_type: str
    content_disposition: str


def validate_key(key: str) -> str:
    """Validate a single root-level key without rewriting it.

    Raises:
        ValueError: If the key is empty after trimming, is not a single segment,
            contains a control character, or exceeds the application length limit.
    """
    if not key.strip():
        raise ValueError("File name must not be empty")
    if key in {".", ".."}:
        raise ValueError("File name must not be '.' or '..'")
    if "/" in key or "\\" in key:
        raise ValueError("File name must be a single path segment")
    if any(unicodedata.category(character) == "Cc" for character in key):
        raise ValueError("File name must not contain control characters")
    if len(key) > MAX_KEY_CODEPOINTS:
        raise ValueError(f"File name must not exceed {MAX_KEY_CODEPOINTS} code points")
    return key


def choose_key(name: str, existing_keys: set[str]) -> str:
    """Return the original valid name or its first free ``-N`` variant.

    Existing numeric suffixes are ordinary stem content; this function never parses,
    collapses, truncates, or normalizes them.

    Raises:
        ValueError: If ``name`` is invalid, or if the next generated ``-N`` candidate
            exceeds 200 code points, including when the first ``-1`` suffix is already
            too long.
    """
    validate_key(name)
    if name not in existing_keys:
        return name

    stem, suffix = os.path.splitext(name)
    for index in count(1):
        candidate = f"{stem}-{index}{suffix}"
        if len(candidate) > MAX_KEY_CODEPOINTS:
            raise ValueError(f"Generated file name exceeds {MAX_KEY_CODEPOINTS} code points")
        if candidate not in existing_keys:
            return candidate
    raise AssertionError("unreachable")


async def _list_file_keys(operator: AsyncOperator) -> list[str]:
    """List direct file keys once, ignoring directory entries.

    This performs OpenDAL list I/O and propagates its concrete public storage errors.
    """
    entries = await operator.list("")
    return [entry.path async for entry in entries if entry.metadata.is_file]


async def list_files(operator: AsyncOperator) -> FileList:
    """List and return every direct file in ascending key order.

    This performs one OpenDAL list operation, never presigns, and propagates concrete
    public ``opendal.exceptions`` errors.
    """
    keys = await _list_file_keys(operator)
    return FileList(files=[PasteFile(key=key) for key in sorted(keys)])


async def issue_upload_grant(
    operator: AsyncOperator, request: UploadRequest, *, ttl_seconds: int
) -> UploadGrant:
    """Choose a key from one list snapshot and presign a direct write.

    The OpenDAL calls list storage and create a signed request but do not create an
    object or reserve the chosen key. Binary content type and an ASCII-safe RFC 8187
    attachment filename are included independently when the backend advertises support
    for each option.

    Raises:
        ValueError: If the requested or generated key is invalid.
        opendal.exceptions.*: A concrete public storage error from listing or
            presigning.
    """
    validate_key(request.name)
    existing_keys = set(await _list_file_keys(operator))
    key = choose_key(request.name, existing_keys)
    capabilities = operator.capability()
    options = _PresignWriteOptions()
    if capabilities.write_with_content_type:
        options["content_type"] = DEFAULT_CONTENT_TYPE
    if capabilities.write_with_content_disposition:
        encoded_key = quote(key, safe="")
        options["content_disposition"] = f"attachment; filename*=UTF-8''{encoded_key}"
    presigned = await operator.presign_write(
        key,
        ttl_seconds,
        **options,
    )
    return UploadGrant(
        key=key,
        method=presigned.method,
        upload_url=presigned.url,
        headers=dict(presigned.headers),
    )


async def issue_download_grant(
    operator: AsyncOperator, request: DownloadRequest, *, ttl_seconds: int
) -> DownloadGrant:
    """Presign a read for an exact valid key without reading or checking the object.

    Raises:
        ValueError: If the requested key is invalid.
        opendal.exceptions.*: A concrete public storage error from presigning.
    """
    key = validate_key(request.key)
    presigned = await operator.presign_read(key, ttl_seconds)
    return DownloadGrant(key=key, download_url=presigned.url)
