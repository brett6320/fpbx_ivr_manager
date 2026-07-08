"""FastAPI entrypoint."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.auth import backend
from app.config import settings
from app.web.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    backend.validate_config()
    if backend.kind() == "local":
        from app.auth import local
        local.seed_admin()
    yield


app = FastAPI(title="FusionPBX IVR Manager", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    https_only=True,
    same_site="lax",
)
app.include_router(router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
