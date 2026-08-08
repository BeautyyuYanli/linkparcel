"""Synchronous CLI for authenticated direct uploads.

Business authorization is attached only to explicit control-plane requests. Storage
requests use solely the method and signed headers returned by the upload grant.
"""

import logging
import os
import stat
from pathlib import Path
from typing import Annotated, BinaryIO, cast

import httpx2
import typer
from puremagic.main import PureError, from_file  # pyright: ignore[reportUnknownVariableType]
from pydantic import BaseModel

from oh_my_pastebin.models import (
    DownloadGrant,
    DownloadRequest,
    UploadGrant,
    UploadRequest,
)

logger = logging.getLogger(__name__)
DEFAULT_API_URL = "http://127.0.0.1:59000"
DEFAULT_CONTENT_TYPE = "application/octet-stream"
app = typer.Typer(no_args_is_help=True)


class CliError(RuntimeError):
    """A safe, concise failure intended for CLI stderr."""


@app.callback()
def main() -> None:
    """Upload files to a private Oh My Pastebin deployment."""


def _content_type(path: Path) -> str:
    """Read the file for best-effort MIME detection.

    Identification failures return the default type; file access errors propagate as
    ``OSError`` for the command boundary to report.
    """
    try:
        detected = from_file(str(path), mime=True)
    except (PureError, ValueError):
        return DEFAULT_CONTENT_TYPE
    return detected or DEFAULT_CONTENT_TYPE


def _api_token() -> str:
    """Load the business token from the process environment.

    Raises:
        CliError: If ``OMP_API_TOKEN`` is absent or empty.
    """
    token = os.getenv("OMP_API_TOKEN")
    if token is None or token == "":
        raise CliError("OMP_API_TOKEN 未设置或为空")
    return token


def _api_endpoint(server: str, path: str) -> str:
    """Join a configured control-plane base with one fixed API path."""
    return f"{server.rstrip('/')}{path}"


def _response_detail(response: httpx2.Response) -> str:
    """Return the standard FastAPI detail when available, otherwise only the status."""
    try:
        payload = cast(object, response.json())
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = cast(dict[object, object], payload).get("detail")
        if isinstance(detail, str):
            return f"HTTP {response.status_code}: {detail}"
    return f"HTTP {response.status_code}"


def _post_model[ModelT: BaseModel](
    client: httpx2.Client,
    endpoint: str,
    token: str,
    request: BaseModel,
    response_type: type[ModelT],
) -> ModelT:
    """POST one authenticated model and validate the successful response shape.

    This performs control-plane HTTP I/O and attaches the business Bearer token only
    to that request.

    Raises:
        CliError: If the request fails, returns non-2xx, or has a malformed success
            body.
    """
    try:
        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            json=request.model_dump(mode="json"),
        )
    except (httpx2.RequestError, httpx2.InvalidURL) as error:
        logger.debug("Control-plane request failed: %s", type(error).__name__)
        raise CliError("无法连接控制面") from None
    if not response.is_success:
        raise CliError(f"控制面请求失败（{_response_detail(response)}）")
    try:
        return response_type.model_validate(response.json())
    except ValueError:
        raise CliError("控制面返回了无效响应") from None


def _upload_to_storage(
    client: httpx2.Client,
    grant: UploadGrant,
    file: BinaryIO,
    content_length: int,
) -> None:
    """Stream an open file to storage using only the signed grant components.

    The caller owns and closes ``file``. This function reads it while performing the
    storage HTTP request.

    Raises:
        CliError: If the storage URL is invalid, transport fails, or returns non-2xx.
        OSError: If reading the supplied file fails.
    """
    headers = httpx2.Headers(grant.headers)
    headers["Content-Length"] = str(content_length)
    try:
        response = client.request(
            grant.method,
            grant.upload_url,
            headers=headers,
            content=file,
        )
    except (httpx2.RequestError, httpx2.InvalidURL) as error:
        logger.debug("Storage request failed: %s", type(error).__name__)
        raise CliError("上游上传失败（网络错误）") from None
    if not response.is_success:
        raise CliError(f"上游上传失败（HTTP {response.status_code}）")


@app.command()
def upload(
    path: Annotated[Path, typer.Argument(help="Local file to upload")],
    server: Annotated[
        str | None,
        typer.Option("--server", help="Control-plane base URL; defaults to OMP_API_URL"),
    ] = None,
) -> None:
    """Upload one file and emit its final key and download URL.

    Reads the local file, calls the control plane twice, streams bytes to storage, and
    writes one-line JSON to stdout on success. Handled failures write a safe message to
    stderr.

    Storage may already contain the object when download-grant issuance or returned-key
    verification fails. In that partial-success case stdout remains empty and stderr
    identifies the uploaded key.

    Raises:
        typer.Exit: With a nonzero code after any handled CLI failure.
    """
    try:
        if not path.is_file():
            raise CliError(f"不是可读普通文件：{path}")
        try:
            content_type = _content_type(path)
        except OSError as error:
            logger.debug("MIME detection could not read file: %s", type(error).__name__)
            raise CliError(f"无法读取本地文件：{path}") from None

        token = _api_token()
        api_url = server or os.getenv("OMP_API_URL") or DEFAULT_API_URL
        with httpx2.Client() as client:
            try:
                with path.open("rb") as file:
                    file_stat = os.fstat(file.fileno())
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise CliError(f"不是普通文件：{path}")
                    grant = _post_model(
                        client,
                        _api_endpoint(api_url, "/api/v1/uploads"),
                        token,
                        UploadRequest(name=path.name, content_type=content_type),
                        UploadGrant,
                    )
                    _upload_to_storage(client, grant, file, file_stat.st_size)
            except OSError as error:
                logger.debug("Local file read failed: %s", type(error).__name__)
                raise CliError(f"无法读取本地文件：{path}") from None
            try:
                download = _post_model(
                    client,
                    _api_endpoint(api_url, "/api/v1/downloads"),
                    token,
                    DownloadRequest(key=grant.key),
                    DownloadGrant,
                )
            except CliError:
                raise CliError(f"对象已上传为 `{grant.key}`，但下载链接签发失败") from None
            if download.key != grant.key:
                raise CliError(f"对象已上传为 `{grant.key}`，但下载链接返回了不同的 key")
        typer.echo(download.model_dump_json())
    except CliError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
