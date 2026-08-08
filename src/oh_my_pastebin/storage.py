"""OpenDAL construction, startup checks, and runtime ownership.

One async operator belongs to the application lifespan and is reused by every route.
The Python binding has no explicit close contract, so shutdown only releases this state.
"""

from dataclasses import dataclass

from opendal import AsyncOperator

from oh_my_pastebin.config import Settings

REQUIRED_CAPABILITIES = (
    "list",
    "presign_write",
    "presign_read",
    "write_with_content_type",
)


class UnsupportedStorageError(RuntimeError):
    """Raised when a configured backend cannot satisfy the control-plane contract."""


@dataclass(frozen=True, slots=True)
class AppResources:
    """Typed resources owned by one running FastAPI application."""

    settings: Settings
    operator: AsyncOperator


def create_operator(storage_uri: str) -> AsyncOperator:
    """Construct an async operator from the unchanged opaque storage URI.

    No backend check is performed here. Concrete public ``opendal.exceptions`` errors
    propagate when OpenDAL cannot construct the configured service.
    """
    return AsyncOperator.from_uri(storage_uri)  # pyright: ignore[reportUnknownMemberType]


async def ensure_operator_ready(operator: AsyncOperator) -> None:
    """Validate required capabilities, then perform the backend readiness check.

    ``operator.check()`` may perform external I/O.

    Raises:
        UnsupportedStorageError: If any required capability is absent.
        opendal.exceptions.*: A concrete public storage error from the backend
            readiness check.
    """
    capabilities = operator.capability()
    missing = [name for name in REQUIRED_CAPABILITIES if not getattr(capabilities, name)]
    if missing:
        joined = ", ".join(missing)
        raise UnsupportedStorageError(f"Storage backend lacks required capabilities: {joined}")
    await operator.check()
