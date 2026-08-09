"""Synchronous CLI for authenticated direct uploads.

Business authorization is attached only to explicit control-plane requests. Storage
requests use solely the method and signed headers returned by the upload grant.

CLI configuration lives at ``$XDG_CONFIG_HOME/omp/env`` (defaulting to
``~/.config/omp/env``). Process environment values override the file.
"""

import logging
import os
import stat
from pathlib import Path
from typing import Annotated, BinaryIO, cast

import httpx2
import typer
from dotenv import set_key
from pydantic import AnyHttpUrl, BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from oh_my_pastebin.models import (
    DownloadGrant,
    DownloadRequest,
    UploadGrant,
    UploadRequest,
)

logger = logging.getLogger(__name__)
DEFAULT_API_URL = "http://127.0.0.1:59000"
app = typer.Typer(no_args_is_help=True)
AuthToken = Annotated[SecretStr, Field(min_length=1)]


class CliError(RuntimeError):
    """A safe, concise failure intended for CLI stderr."""


class AuthConfig(BaseModel):
    """Values validated before an auth configuration write."""

    api_url: AnyHttpUrl
    api_token: AuthToken


class CliSettings(BaseSettings):
    """CLI settings loaded from process environment and the XDG env file."""

    model_config = SettingsConfigDict(env_prefix="OMP_", extra="ignore")

    api_url: AnyHttpUrl = AnyHttpUrl(DEFAULT_API_URL)
    api_token: AuthToken

    def __init__(self, env_file: Path) -> None:
        """Load process environment over the supplied dotenv file."""
        super().__init__(_env_file=env_file, _env_file_encoding="utf-8")


@app.callback()
def main() -> None:
    """Upload files to a private Oh My Pastebin deployment."""


def _config_path() -> Path:
    """Return the per-user CLI configuration path."""
    config_home = os.getenv("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "omp" / "env"


def _load_cli_settings() -> CliSettings:
    """Load CLI settings with process environment taking precedence over the XDG file.

    Raises:
        CliError: If authentication configuration is missing or invalid, including an
            invalid URL.
    """
    try:
        return CliSettings(_config_path())
    except ValidationError:
        raise CliError("Authentication configuration is missing or invalid") from None


def _write_auth_config(url: str, token: str) -> Path:
    """Validate and persist CLI authentication settings with private permissions.

    Ensures the config directory has mode ``0700`` and the file has mode ``0600``.
    Symlink and non-regular config targets are rejected.

    Returns:
        The configuration file path.

    Raises:
        CliError: If validation fails or the file cannot be written safely.
    """
    try:
        config = AuthConfig.model_validate({"api_url": url, "api_token": token})
    except ValidationError:
        raise CliError("Authentication configuration is missing or invalid") from None

    path = _config_path()
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise CliError("CLI config path is not a regular file")
        if path.exists():
            path.chmod(0o600)
        set_key(path, "OMP_API_URL", str(config.api_url))
        path.chmod(0o600)
        set_key(path, "OMP_API_TOKEN", config.api_token.get_secret_value())
        path.chmod(0o600)
    except OSError as error:
        logger.debug("CLI config write failed: %s", type(error).__name__)
        raise CliError("Unable to write CLI config") from None
    return path


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
        raise CliError("Unable to connect to control plane") from None
    if not response.is_success:
        raise CliError(f"Control-plane request failed ({_response_detail(response)})")
    try:
        return response_type.model_validate(response.json())
    except ValueError:
        raise CliError("Control plane returned an invalid response") from None


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
        raise CliError("Upstream upload failed (network error)") from None
    if not response.is_success:
        raise CliError(f"Upstream upload failed (HTTP {response.status_code})")


@app.command()
def auth(
    url: Annotated[str, typer.Option("--url", help="Control-plane base URL")],
    token: Annotated[str, typer.Option("--token", help="Business authorization token")],
) -> None:
    """Save CLI authentication configuration."""
    try:
        path = _write_auth_config(url, token)
        typer.echo(f"Authentication config saved to {path}")
    except CliError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None


@app.command()
def upload(
    path: Annotated[Path, typer.Argument(help="Local file to upload")],
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
            raise CliError(f"Not a readable regular file: {path}")

        settings = _load_cli_settings()
        token = settings.api_token.get_secret_value()
        api_url = str(settings.api_url)
        with httpx2.Client() as client:
            try:
                with path.open("rb") as file:
                    file_stat = os.fstat(file.fileno())
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise CliError(f"Not a regular file: {path}")
                    grant = _post_model(
                        client,
                        _api_endpoint(api_url, "/api/v1/uploads"),
                        token,
                        UploadRequest(name=path.name),
                        UploadGrant,
                    )
                    _upload_to_storage(client, grant, file, file_stat.st_size)
            except OSError as error:
                logger.debug("Local file read failed: %s", type(error).__name__)
                raise CliError(f"Unable to read local file: {path}") from None
            try:
                download = _post_model(
                    client,
                    _api_endpoint(api_url, "/api/v1/downloads"),
                    token,
                    DownloadRequest(key=grant.key),
                    DownloadGrant,
                )
            except CliError:
                raise CliError(
                    f"Object uploaded as `{grant.key}`, but download URL issuance failed"
                ) from None
            if download.key != grant.key:
                raise CliError(
                    f"Object uploaded as `{grant.key}`, but download URL returned a different key"
                )
        typer.echo(download.model_dump_json())
    except CliError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
