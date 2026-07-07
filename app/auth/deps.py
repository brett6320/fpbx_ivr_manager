"""FastAPI auth dependency: require a logged-in session user."""
from __future__ import annotations

from fastapi import Request
from starlette.exceptions import HTTPException


def current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        # signal the caller to redirect to login
        raise HTTPException(status_code=307, headers={"Location": "/auth/login"})
    return user


def optional_user(request: Request) -> dict | None:
    return request.session.get("user")
