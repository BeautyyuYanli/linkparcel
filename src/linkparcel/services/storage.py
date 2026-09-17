"""Construct and check OpenDAL operators for either controller.

Callers own operator lifetimes. The Python binding has no explicit close contract.
OpenDAL exposes concrete errors as siblings, so consumers must catch the public
family below rather than only ``exceptions.Error``.
"""

from opendal import AsyncOperator, exceptions

OPENDAL_ERRORS: tuple[type[Exception], ...] = (
    exceptions.Error,
    exceptions.Unexpected,
    exceptions.Unsupported,
    exceptions.ConfigInvalid,
    exceptions.NotFound,
    exceptions.PermissionDenied,
    exceptions.IsADirectory,
    exceptions.NotADirectory,
    exceptions.AlreadyExists,
    exceptions.IsSameFile,
    exceptions.ConditionNotMatch,
    exceptions.RateLimited,
    exceptions.RangeNotSatisfied,
)

REQUIRED_CAPABILITIES = (
    "list",
    "presign_write",
    "presign_read",
)


class UnsupportedStorageError(RuntimeError):
    """Raised when a configured backend cannot satisfy the file-sharing contract."""


def create_operator(storage_uri: str) -> AsyncOperator:
    """Construct an async operator from the unchanged opaque storage URI.

    No backend check is performed here. Concrete public ``opendal.exceptions`` errors
    propagate when OpenDAL cannot construct the configured service.
    """
    return AsyncOperator.from_uri(storage_uri)  # pyright: ignore[reportUnknownMemberType]


async def ensure_operator_ready(operator: AsyncOperator) -> None:
    """Validate required operations, then perform the backend readiness check.

    ``operator.check()`` may perform external I/O.
    Content type and disposition support are optional upload enhancements.

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
