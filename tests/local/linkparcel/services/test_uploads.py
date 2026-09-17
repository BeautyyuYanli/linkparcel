"""Local upload contracts at real file, OpenDAL, and HTTP transport boundaries."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import httpx2
import pytest
from opendal import AsyncOperator, exceptions
from opendal.types import Entry

from linkparcel.config import StorageSettings
from linkparcel.services import uploads
from linkparcel.services.uploads import UploadError, upload_file


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Model OpenDAL's public async methods and signed request attributes."""

    async def entries(_path: str) -> AsyncIterator[Entry]:
        yield cast(
            Entry, SimpleNamespace(path="report.pdf", metadata=SimpleNamespace(is_file=True))
        )

    fake = Mock(spec=AsyncOperator)
    fake.capability.return_value = SimpleNamespace(
        list=True,
        presign_write=True,
        presign_read=True,
        write_with_content_type=False,
        write_with_content_disposition=False,
    )
    fake.check = AsyncMock()
    fake.list = AsyncMock(side_effect=entries)
    fake.presign_write = AsyncMock(
        return_value=SimpleNamespace(
            method="POST", url="https://storage.test/upload", headers={"x-signed": "yes"}
        )
    )
    fake.presign_read = AsyncMock(
        return_value=SimpleNamespace(method="GET", url="https://storage.test/download", headers={})
    )
    monkeypatch.setattr(uploads, "create_operator", Mock(return_value=cast(AsyncOperator, fake)))
    return fake


@pytest.mark.parametrize("content", [b"", b"%PDF\n" * 20000])
async def test_upload_sends_signed_request_and_returns_final_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator: Mock, content: bytes
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(content)
    requests: list[httpx2.Request] = []

    class StreamingTransport(httpx2.BaseTransport):
        def handle_request(self, request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            assert request.url == "https://storage.test/upload"
            assert request.method == "POST"
            assert request.headers["x-signed"] == "yes"
            assert request.headers["content-length"] == str(len(content))
            assert "authorization" not in request.headers
            # MockTransport preloads the request; consume the real outgoing stream
            # here so reading the whole file into one bytes body fails this contract.
            assert isinstance(request.stream, httpx2.SyncByteStream)
            chunks = list(request.stream)
            assert b"".join(chunks) == content
            if content:
                assert len(chunks) > 1
            return httpx2.Response(200)

    client = httpx2.Client(transport=StreamingTransport())
    monkeypatch.setattr(uploads.httpx2, "Client", lambda: client)
    settings = StorageSettings(
        storage_uri="opaque-uri", upload_url_ttl_seconds=123, download_url_ttl_seconds=456
    )

    result = await upload_file(settings, path)

    assert result.model_dump() == {
        "key": "report-1.pdf",
        "download_url": "https://storage.test/download",
    }
    operator.check.assert_awaited_once()
    operator.presign_write.assert_awaited_once_with("report-1.pdf", 123)
    operator.presign_read.assert_awaited_once_with("report-1.pdf", 456)
    assert len(requests) == 1
    assert client.is_closed


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
async def test_invalid_local_path_fails_before_storage_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = tmp_path / kind
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    factory = Mock()
    monkeypatch.setattr(uploads, "create_operator", factory)

    with pytest.raises(UploadError, match="Not a readable regular file"):
        await upload_file(StorageSettings(storage_uri="opaque-uri"), path)

    factory.assert_not_called()


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ("construct", "Storage backend initialization failed"),
        ("check", "Storage backend initialization failed"),
        ("list", "Unable to issue upload URL"),
        ("presign_write", "Unable to issue upload URL"),
        ("presign_read", "Object uploaded as `report-1.pdf`, but download URL issuance failed"),
    ],
)
async def test_backend_errors_are_safe_and_identify_partial_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operator: Mock,
    operation: str,
    message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"data")
    secret = "storage-uri-with-secret"
    if operation == "construct":
        monkeypatch.setattr(uploads, "create_operator", Mock(side_effect=ValueError(secret)))
    else:
        getattr(operator, operation).side_effect = exceptions.PermissionDenied(secret)
    client = httpx2.Client(transport=httpx2.MockTransport(lambda _: httpx2.Response(200)))
    monkeypatch.setattr(uploads.httpx2, "Client", lambda: client)

    with pytest.raises(UploadError) as raised:
        await upload_file(StorageSettings(storage_uri=secret), path)

    assert str(raised.value) == message
    assert secret not in caplog.text
    if operation == "presign_read":
        assert client.is_closed
        operator.presign_write.assert_awaited_once()
        operator.presign_read.assert_awaited_once()


@pytest.mark.parametrize("failure", ["status", "transport", "invalid-url"])
async def test_failed_transfer_never_issues_download_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator: Mock, failure: str
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"data")
    if failure == "invalid-url":
        operator.presign_write.return_value.url = "http://storage.test:invalid/upload"

    def handle(request: httpx2.Request) -> httpx2.Response:
        if failure == "transport":
            raise httpx2.WriteError("secret signed URL", request=request)
        return httpx2.Response(403)

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(uploads.httpx2, "Client", lambda: client)

    with pytest.raises(UploadError) as raised:
        await upload_file(StorageSettings(storage_uri="opaque-uri"), path)

    expected = "HTTP 403" if failure == "status" else "network error"
    assert str(raised.value) == f"Upstream upload failed ({expected})"
    operator.presign_read.assert_not_awaited()
    assert client.is_closed


@pytest.mark.parametrize("operation", ["upload", "download"])
async def test_native_presign_argument_overflow_is_safe_and_preserves_uploaded_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operator: Mock, operation: str
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"data")
    # Exercise the real native integer conversion without contacting a storage service.
    native_operator = AsyncOperator("memory")
    ttl = 1 << 64
    settings = StorageSettings(
        storage_uri="opaque-uri",
        upload_url_ttl_seconds=ttl if operation == "upload" else 300,
        download_url_ttl_seconds=ttl if operation == "download" else 3600,
    )
    if operation == "upload":
        operator.presign_write.side_effect = native_operator.presign_write
    else:
        operator.presign_read.side_effect = native_operator.presign_read
    client = httpx2.Client(transport=httpx2.MockTransport(lambda _: httpx2.Response(200)))
    monkeypatch.setattr(uploads.httpx2, "Client", lambda: client)

    with pytest.raises(UploadError) as raised:
        await upload_file(settings, path)

    if operation == "upload":
        assert str(raised.value) == "Unable to issue upload URL"
        operator.presign_read.assert_not_awaited()
    else:
        assert str(raised.value) == (
            "Object uploaded as `report-1.pdf`, but download URL issuance failed"
        )
        assert client.is_closed
