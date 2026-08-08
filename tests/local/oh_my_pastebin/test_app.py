"""Authenticated API behavior exercised through FastAPI's public ASGI boundary."""

from collections.abc import AsyncIterator, Callable
from typing import cast

import pytest
from fastapi.testclient import TestClient
from opendal import AsyncOperator, exceptions
from opendal.types import Entry, PresignedRequest
from pydantic import SecretStr

from oh_my_pastebin.app import create_app
from oh_my_pastebin.config import Settings


class _Capabilities:
    list = True
    presign_write = True
    presign_read = True
    write_with_content_type = True


class _Metadata:
    is_file = True


class _Entry:
    def __init__(self, path: str) -> None:
        self.path = path
        self.metadata = _Metadata()


class _PresignedRequest:
    def __init__(self, method: str, url: str, headers: dict[str, str]) -> None:
        self.method = method
        self.url = url
        self.headers = headers


class _Operator:
    def capability(self) -> _Capabilities:
        return _Capabilities()

    async def check(self) -> None:
        pass

    async def list(self, path: str) -> AsyncIterator[Entry]:
        async def iterate() -> AsyncIterator[Entry]:
            yield cast(Entry, _Entry("hello.txt"))

        return iterate()

    async def presign_write(
        self, path: str, expire_second: int, *, content_type: str | None = None
    ) -> PresignedRequest:
        return cast(
            PresignedRequest,
            _PresignedRequest("PUT", "https://storage.test/write", {"x-signed": "yes"}),
        )

    async def presign_read(self, path: str, expire_second: int) -> PresignedRequest:
        return cast(PresignedRequest, _PresignedRequest("GET", "https://storage.test/read", {}))


class _ListFailure(_Operator):
    async def list(self, path: str) -> AsyncIterator[Entry]:
        raise exceptions.PermissionDenied("s3://secret-storage-configuration")


class _WriteFailure(_Operator):
    async def presign_write(
        self, path: str, expire_second: int, *, content_type: str | None = None
    ) -> PresignedRequest:
        raise exceptions.RateLimited("s3://secret-storage-configuration")


class _ReadFailure(_Operator):
    async def presign_read(self, path: str, expire_second: int) -> PresignedRequest:
        raise exceptions.Unexpected("s3://secret-storage-configuration")


STARTUP_SECRET = "s3://secret-startup-configuration"


class _CheckFailure(_Operator):
    async def check(self) -> None:
        raise exceptions.PermissionDenied(STARTUP_SECRET)


def _construction_failure(_storage_uri: str) -> AsyncOperator:
    raise exceptions.ConfigInvalid(STARTUP_SECRET)


def _check_failure(_storage_uri: str) -> AsyncOperator:
    return cast(AsyncOperator, _CheckFailure())


def _client(token: str = "secret", operator: AsyncOperator | None = None) -> TestClient:
    settings = Settings(storage_uri="opaque", api_token=SecretStr(token))
    fake = operator or cast(AsyncOperator, _Operator())
    application = create_app(
        settings_factory=lambda: settings,
        operator_factory=lambda _: fake,
    )
    return TestClient(application)


def test_files_route_rejects_all_invalid_credential_forms() -> None:
    with _client() as client:
        for headers in (
            {},
            {"Authorization": "Basic secret"},
            {"Authorization": "Bearer wrong"},
            {"Authorization": "Bearer "},
        ):
            response = client.get("/api/v1/files", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/uploads", {"name": "hello.txt"}),
        ("/api/v1/downloads", {"key": "hello.txt"}),
    ],
)
def test_other_api_routes_require_bearer_token(
    path: str,
    payload: dict[str, str],
) -> None:
    with _client() as client:
        response = client.post(
            path,
            headers={"Authorization": "Bearer wrong"},
            json=payload,
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_api_routes_return_wire_models_and_disable_grant_caching() -> None:
    storage_uri = "opaque://bucket/root?credential=server-only"
    settings = Settings(storage_uri=storage_uri, api_token=SecretStr("secret"))
    operator = cast(AsyncOperator, _Operator())
    factory_calls: list[str] = []

    def operator_factory(received_uri: str) -> AsyncOperator:
        factory_calls.append(received_uri)
        return operator

    application = create_app(
        settings_factory=lambda: settings,
        operator_factory=operator_factory,
    )
    headers = {"Authorization": "Bearer secret"}
    with TestClient(application) as client:
        files = client.get("/api/v1/files", headers=headers)
        upload = client.post(
            "/api/v1/uploads",
            headers=headers,
            json={"name": "hello.txt", "content_type": "text/plain"},
        )
        download = client.post(
            "/api/v1/downloads", headers=headers, json={"key": upload.json()["key"]}
        )

    assert files.json() == {"files": [{"key": "hello.txt"}]}
    assert upload.status_code == 200
    assert upload.json()["key"] == "hello-1.txt"
    assert upload.headers["cache-control"] == "no-store"
    assert download.json() == {
        "key": "hello-1.txt",
        "download_url": "https://storage.test/read",
    }
    assert download.headers["cache-control"] == "no-store"
    assert factory_calls == [storage_uri]


def test_api_rejects_invalid_key_with_422() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/downloads",
            headers={"Authorization": "Bearer secret"},
            json={"key": "nested/file.txt"},
        )

    assert response.status_code == 422


def test_api_rejects_invalid_upload_name_with_422() -> None:
    with _client() as client:
        response = client.post(
            "/api/v1/uploads",
            headers={"Authorization": "Bearer secret"},
            json={"name": "nested/file.txt", "content_type": "text/plain"},
        )

    assert response.status_code == 422


def test_non_ascii_configured_token_still_rejects_invalid_credentials_cleanly() -> None:
    with _client("private-\u2603-token") as client:
        response = client.get(
            "/api/v1/files",
            headers={"Authorization": "Bearer wrong"},
        )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("operator", "method", "path", "payload"),
    [
        (cast(AsyncOperator, _ListFailure()), "GET", "/api/v1/files", None),
        (
            cast(AsyncOperator, _WriteFailure()),
            "POST",
            "/api/v1/uploads",
            {"name": "new.txt", "content_type": "text/plain"},
        ),
        (
            cast(AsyncOperator, _ReadFailure()),
            "POST",
            "/api/v1/downloads",
            {"key": "hello.txt"},
        ),
    ],
)
def test_concrete_storage_errors_map_to_opaque_503_without_logging_their_message(
    caplog: pytest.LogCaptureFixture,
    operator: AsyncOperator,
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    with _client(operator=operator) as client:
        response = client.request(
            method,
            path,
            headers={"Authorization": "Bearer secret"},
            json=payload,
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Storage service unavailable"}
    assert "secret-storage-configuration" not in caplog.text


@pytest.mark.parametrize("operator_factory", [_construction_failure, _check_failure])
def test_storage_startup_failure_is_sanitized(
    caplog: pytest.LogCaptureFixture,
    operator_factory: Callable[[str], AsyncOperator],
) -> None:
    settings = Settings(storage_uri=STARTUP_SECRET, api_token=SecretStr("secret"))
    application = create_app(
        settings_factory=lambda: settings,
        operator_factory=operator_factory,
    )

    with pytest.raises(RuntimeError, match="^Storage backend initialization failed$"):
        with TestClient(application):
            pass

    assert STARTUP_SECRET not in caplog.text
