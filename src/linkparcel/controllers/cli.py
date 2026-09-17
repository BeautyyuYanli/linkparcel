"""Command-line adapter for local services and XDG storage configuration.

Uploads need storage credentials but no LinkParcel server or application token.
Only successful uploads write JSON to stdout; handled failures use stderr.
"""

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from linkparcel.config import ConfigurationError, load_cli_settings, write_cli_config
from linkparcel.services.uploads import UploadError, upload_file

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Upload files directly to private storage with LinkParcel."""


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
    """Upload through local services and emit one JSON key/download URL record.

    Configuration and service errors produce a nonzero exit code with empty stdout.
    A failure after the upload identifies the stored key in its stderr message.
    """
    try:
        download = asyncio.run(upload_file(load_cli_settings(), path))
    except (ConfigurationError, UploadError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(download.model_dump_json())
