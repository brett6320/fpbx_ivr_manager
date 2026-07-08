"""FastAPI entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
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


@app.get("/healthz")
def healthz():
    return {"ok": True}
