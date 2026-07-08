"""FastAPI entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.auth import backend
from app.config import settings
from app.web.routes import router

log = logging.getLogger("fpbx_ivr_manager")


def _run_schema_check() -> None:
    """Verify the connected FusionPBX DB has the tables/columns we need."""
    mode = settings.fpbx_schema_check.lower()
    if mode == "off":
        return
    from app.fpbx import compat

    try:
        result = compat.check_schema()
    except Exception as e:  # noqa: BLE001 - DB may be briefly unreachable at boot
        msg = f"FusionPBX schema check could not run: {type(e).__name__}"
        if mode == "strict":
            raise RuntimeError(msg) from e
        log.warning(msg)
        return

    if result["ok"]:
        log.info("FusionPBX schema check passed (%s)", compat.SUPPORTED_RANGE)
        return

    missing = result["missing_tables"] + result["missing_columns"]
    msg = (
        f"FusionPBX schema check found missing objects: {missing}. "
        f"Supported: {compat.SUPPORTED_RANGE}."
    )
    if mode == "strict":
        raise RuntimeError(msg)
    log.warning(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend.validate_config()
    if settings.dev_mode:
        log.warning("DEV_MODE is on — MFA is not enforced for local admins. Do not use in production.")
    if backend.kind() == "local":
        from app.auth import local
        local.seed_admin()
    _run_schema_check()
    yield


app = FastAPI(title="FusionPBX IVR Manager", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    https_only=settings.session_https_only,
    same_site="lax",
)
app.include_router(router)


@app.exception_handler(StarletteHTTPException)
async def styled_http_exception(request: Request, exc: StarletteHTTPException):
    """Render 4xx/5xx as a themed page (inside the app shell) for browser
    navigations; keep redirects and JSON API responses intact."""
    # preserve redirects (e.g. the auth guard's 307 -> /auth/login)
    location = None
    for k, v in (exc.headers or {}).items():
        if k.lower() == "location":
            location = v
    if location:
        return RedirectResponse(location, status_code=exc.status_code)

    accept = request.headers.get("accept", "")
    if "text/html" not in accept:  # fetch()/API callers -> JSON
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    from app.web.routes import templates

    titles = {400: "Invalid request", 401: "Sign in required",
              403: "Not permitted", 404: "Not found", 409: "Refused",
              500: "Something went wrong"}
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status": exc.status_code, "title": titles.get(exc.status_code),
         "message": exc.detail or "The request could not be completed."},
        status_code=exc.status_code,
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}
