"""FastAPI control plane for authenticated listing and direct-transfer grants.

The API never proxies object bytes. Its lifespan owns one OpenDAL async operator;
routes only list keys or issue short-lived presigned requests.

Business operations live in ``linkparcel.services``; only this controller validates
the application Bearer token and maps service failures to HTTP responses.
"""

import logging
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Never, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from opendal import AsyncOperator

from linkparcel.config import ServerSettings
from linkparcel.models import (
    DownloadGrant,
    DownloadRequest,
    FileList,
    UploadGrant,
    UploadRequest,
)
from linkparcel.services.files import issue_download_grant, issue_upload_grant, list_files
from linkparcel.services.storage import (
    OPENDAL_ERRORS,
    UnsupportedStorageError,
    create_operator,
    ensure_operator_ready,
)

logger = logging.getLogger(__name__)
SettingsFactory = Callable[[], ServerSettings]
OperatorFactory = Callable[[str], AsyncOperator]


@dataclass(frozen=True, slots=True)
class AppResources:
    """Settings and one operator owned by a running server's lifespan.

    Requests share the operator on the application's event loop. The OpenDAL Python
    binding has no close method; shutdown releases the application state reference.
    """

    settings: ServerSettings
    operator: AsyncOperator


bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def require_api_token(request: Request, credentials: BearerCredentials) -> None:
    """Accept only the application's Bearer token and expose no failure distinctions."""
    resources = cast(AppResources, request.app.state.resources)
    supplied = credentials.credentials if credentials is not None else ""
    expected = resources.settings.api_token.get_secret_value()
    if credentials is None or not secrets.compare_digest(
        supplied.encode(),
        expected.encode(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_token)])


def _resources(request: Request) -> AppResources:
    """Return the lifespan-owned resources for a request."""
    return cast(AppResources, request.app.state.resources)


def _storage_unavailable(operation: str, error: Exception, *, key: str | None = None) -> Never:
    """Log safe operation context and raise the API's opaque storage failure."""
    logger.error(
        "Storage operation failed: operation=%s key=%r error_type=%s",
        operation,
        key,
        type(error).__name__,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Storage service unavailable",
    ) from None


@router.get("/files", response_model=FileList)
async def get_files(request: Request) -> FileList:
    """List every direct file key in ascending order."""
    try:
        return await list_files(_resources(request).operator)
    except OPENDAL_ERRORS as error:  # pyright: ignore[reportUnknownVariableType]
        _storage_unavailable("list", error)


@router.post("/uploads", response_model=UploadGrant)
async def create_upload(request: Request, body: UploadRequest, response: Response) -> UploadGrant:
    """Choose the final key and issue a non-cacheable direct upload grant."""
    resources = _resources(request)
    try:
        grant = await issue_upload_grant(
            resources.operator,
            body,
            ttl_seconds=resources.settings.upload_url_ttl_seconds,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from None
    except OPENDAL_ERRORS as error:  # pyright: ignore[reportUnknownVariableType]
        _storage_unavailable("upload grant", error)
    response.headers["Cache-Control"] = "no-store"
    return grant


@router.post("/downloads", response_model=DownloadGrant)
async def create_download(
    request: Request, body: DownloadRequest, response: Response
) -> DownloadGrant:
    """Issue a non-cacheable read grant without prechecking object existence."""
    resources = _resources(request)
    try:
        grant = await issue_download_grant(
            resources.operator,
            body,
            ttl_seconds=resources.settings.download_url_ttl_seconds,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from None
    except OPENDAL_ERRORS as error:  # pyright: ignore[reportUnknownVariableType]
        _storage_unavailable("download grant", error, key=body.key)
    response.headers["Cache-Control"] = "no-store"
    return grant


def load_settings() -> ServerSettings:
    """Load required settings from the process environment at application startup."""
    return ServerSettings()  # pyright: ignore[reportCallIssue]


def create_app(
    *,
    settings_factory: SettingsFactory = load_settings,
    operator_factory: OperatorFactory = create_operator,
) -> FastAPI:
    """Create an application whose resources are initialized and checked at startup."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings = settings_factory()
        try:
            operator = operator_factory(settings.storage_uri)
            await ensure_operator_ready(operator)
        except UnsupportedStorageError:
            raise
        except Exception:
            # Upstream configuration errors can echo the opaque URI, so startup
            # exposes a stable message without preserving a potentially secret cause.
            raise RuntimeError("Storage backend initialization failed") from None

        application.state.resources = AppResources(settings=settings, operator=operator)
        try:
            yield
        finally:
            del application.state.resources

    application = FastAPI(title="LinkParcel", lifespan=lifespan)
    application.include_router(router)
    return application


app = create_app()
