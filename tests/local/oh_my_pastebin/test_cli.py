"""CLI upload workflow at its filesystem, control-plane, and storage boundaries."""

import json
from pathlib import Path
from typing import cast

import httpx2
import pytest
from typer.testing import CliRunner

from oh_my_pastebin import cli

runner = CliRunner()


def _reject_http_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("HTTP request must not be sent")

    client = httpx2.Client(transport=httpx2.MockTransport(reject))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)


def test_upload_keeps_business_authorization_off_storage_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    content = b"%PDF-1.7\nexample"
    path.write_bytes(content)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/v1/uploads":
            assert request.headers["authorization"] == "Bearer secret"
            assert json.loads(request.content) == {
                "name": "report.pdf",
                "content_type": "application/pdf",
            }
            return httpx2.Response(
                200,
                json={
                    "key": "report-1.pdf",
                    "method": "POST",
                    "upload_url": "https://storage.test/upload",
                    "headers": {"x-signed": "yes"},
                },
            )
        if request.url.host == "storage.test":
            assert request.method == "POST"
            assert "authorization" not in request.headers
            assert request.headers["x-signed"] == "yes"
            assert request.headers["content-length"] == str(len(content))
            assert request.content == content
            return httpx2.Response(200)
        assert request.url.path == "/api/v1/downloads"
        assert request.headers["authorization"] == "Bearer secret"
        assert json.loads(request.content) == {"key": "report-1.pdf"}
        return httpx2.Response(
            200,
            json={
                "key": "report-1.pdf",
                "download_url": "https://storage.test/download",
            },
        )

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(
        cli.app,
        ["upload", str(path)],
        env={"OMP_API_TOKEN": "secret", "OMP_API_URL": "https://api.test"},
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout == (
        '{"key":"report-1.pdf","download_url":"https://storage.test/download"}\n'
    )
    assert len(requests) == 3


def test_upload_falls_back_when_puremagic_cannot_identify_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unknown.bin"
    path.write_bytes(b"unknown")
    observed_content_type: str | None = None

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal observed_content_type
        if request.url.host != "storage.test":
            assert request.url.host == "127.0.0.1"
            assert request.url.port == 59000
        if request.url.path == "/api/v1/uploads":
            payload = cast(dict[str, str], json.loads(request.content))
            observed_content_type = payload["content_type"]
            return httpx2.Response(
                200,
                json={
                    "key": "unknown.bin",
                    "method": "PUT",
                    "upload_url": "https://storage.test/upload",
                    "headers": {},
                },
            )
        if request.url.host == "storage.test":
            return httpx2.Response(204)
        return httpx2.Response(
            200,
            json={"key": "unknown.bin", "download_url": "https://storage.test/download"},
        )

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    def unidentified(*_args: object, **_kwargs: object) -> str:
        raise cli.PureError()

    monkeypatch.setattr(
        cli,
        "from_file",
        unidentified,
    )

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code == 0
    assert observed_content_type == "application/octet-stream"


def test_upload_reports_successful_object_when_download_grant_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/v1/uploads":
            return httpx2.Response(
                200,
                json={
                    "key": "report.pdf",
                    "method": "PUT",
                    "upload_url": "https://storage.test/upload",
                    "headers": {},
                },
            )
        if request.url.host == "storage.test":
            return httpx2.Response(200)
        return httpx2.Response(503, json={"detail": "Storage service unavailable"})

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "对象已上传为 `report.pdf`，但下载链接签发失败" in result.stderr
    assert sum(request.url.path == "/api/v1/uploads" for request in requests) == 1
    assert sum(request.url.host == "storage.test" for request in requests) == 1
    assert sum(request.url.path == "/api/v1/downloads" for request in requests) == 1


def test_upload_maps_invalid_control_plane_url_to_cli_error(tmp_path: Path) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")

    result = runner.invoke(
        cli.app,
        ["upload", str(path)],
        env={"OMP_API_TOKEN": "secret", "OMP_API_URL": "http://api.test:invalid"},
    )

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "无法连接控制面" in result.stderr


def test_upload_maps_invalid_presigned_url_to_storage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/uploads"
        return httpx2.Response(
            200,
            json={
                "key": "report.pdf",
                "method": "PUT",
                "upload_url": "http://storage.test:invalid/upload",
                "headers": {},
            },
        )

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "上游上传失败（网络错误）" in result.stderr


def test_upload_maps_control_plane_transport_failure_to_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")

    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("control plane unavailable", request=request)

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "无法连接控制面" in result.stderr


def test_upload_maps_storage_transport_failure_to_safe_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/uploads":
            return httpx2.Response(
                200,
                json={
                    "key": "report.pdf",
                    "method": "PUT",
                    "upload_url": "https://storage.test/upload",
                    "headers": {},
                },
            )
        assert request.url.host == "storage.test"
        raise httpx2.WriteError("storage unavailable", request=request)

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "上游上传失败（网络错误）" in result.stderr


@pytest.mark.parametrize("token", [None, ""])
def test_upload_requires_nonempty_api_token_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None,
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")
    if token is None:
        monkeypatch.delenv("OMP_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("OMP_API_TOKEN", token)
    _reject_http_requests(monkeypatch)

    result = runner.invoke(cli.app, ["upload", str(path)])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "OMP_API_TOKEN 未设置或为空" in result.stderr


@pytest.mark.parametrize("path_kind", ["missing", "directory"])
def test_upload_rejects_non_file_path_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_kind: str,
) -> None:
    path = tmp_path / path_kind
    if path_kind == "directory":
        path.mkdir()
    _reject_http_requests(monkeypatch)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "不是可读普通文件" in result.stderr


def test_upload_reports_storage_non_success_without_requesting_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/api/v1/uploads":
            return httpx2.Response(
                200,
                json={
                    "key": "report.pdf",
                    "method": "PUT",
                    "upload_url": "https://storage.test/upload",
                    "headers": {},
                },
            )
        assert request.url.host == "storage.test"
        return httpx2.Response(403)

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "上游上传失败（HTTP 403）" in result.stderr
    assert len(requests) == 2


def test_upload_rejects_malformed_successful_control_plane_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")

    def handle(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/v1/uploads"
        return httpx2.Response(200, json={"key": "report.pdf"})

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "控制面返回了无效响应" in result.stderr


def test_upload_rejects_download_grant_for_different_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.7\nexample")

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/uploads":
            return httpx2.Response(
                200,
                json={
                    "key": "report.pdf",
                    "method": "PUT",
                    "upload_url": "https://storage.test/upload",
                    "headers": {},
                },
            )
        if request.url.host == "storage.test":
            return httpx2.Response(200)
        assert request.url.path == "/api/v1/downloads"
        return httpx2.Response(
            200,
            json={
                "key": "different.pdf",
                "download_url": "https://storage.test/download",
            },
        )

    client = httpx2.Client(transport=httpx2.MockTransport(handle))
    monkeypatch.setattr(cli.httpx2, "Client", lambda: client)

    result = runner.invoke(cli.app, ["upload", str(path)], env={"OMP_API_TOKEN": "secret"})

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "对象已上传为 `report.pdf`，但下载链接返回了不同的 key" in result.stderr
