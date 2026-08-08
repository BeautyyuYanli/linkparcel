"""Shared business-token authentication for the protected API router."""

import secrets
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from oh_my_pastebin.storage import AppResources

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
