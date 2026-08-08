"""Pydantic wire models shared by the API and CLI.

URLs intentionally remain strings: the selected storage backend owns their syntax,
and the HTTP client validates them only when making a request.
"""

from pydantic import BaseModel


class PasteFile(BaseModel):
    """A file key listed from the operator root."""

    key: str


class FileList(BaseModel):
    """The complete, sorted file collection returned by the first API version."""

    files: list[PasteFile]


class UploadRequest(BaseModel):
    """The caller's original file name and best-effort content metadata."""

    name: str
    content_type: str = "application/octet-stream"


class UploadGrant(BaseModel):
    """Final key and backend-signed request components.

    The caller separately supplies the request body and its ``Content-Length``.
    """

    key: str
    method: str
    upload_url: str
    headers: dict[str, str]


class DownloadRequest(BaseModel):
    """The exact final key for which a read grant is requested."""

    key: str


class DownloadGrant(BaseModel):
    """The final key and its short-lived direct download URL."""

    key: str
    download_url: str
