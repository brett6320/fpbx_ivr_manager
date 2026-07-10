"""Activity logging middleware.

Records a hash-chained audit entry for every page view (read request) so the
audit log captures navigation/activity in the app, not just modifications.
Write requests (POST/PUT/PATCH/DELETE) are recorded with richer, semantic detail
by the route handlers themselves (e.g. ``user.create`` with the fields changed),
so this middleware only logs safe (read) methods to avoid double-logging.

Runs inside SessionMiddleware (registered before it in app.main) so the session
user is available as the actor.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware

from app import audit
from app.config import settings

# Read methods whose requests count as "activity" worth logging.
_READ_METHODS = {"GET"}
# Never log these (noise or self-referential churn).
_SKIP = {"/healthz", "/favicon.ico"}


def _actor(request) -> str:
    session = request.scope.get("session") or {}
    user = session.get("user") if isinstance(session, dict) else None
    if not user:
        return "anonymous"
    return user.get("email") or user.get("name") or "anonymous"


def _should_log(method: str, path: str) -> bool:
    if method not in _READ_METHODS:
        return False
    # normalize away the mount sub-path (BASE_PATH) before matching
    bp = settings.base_path or ""
    rel = path[len(bp):] if bp and path.startswith(bp) else path
    rel = rel or "/"
    if rel in _SKIP:
        return False
    # don't let viewing the audit log flood the audit log
    if rel.startswith("/admin/audit"):
        return False
    return True


class ActivityLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            if _should_log(request.method, request.url.path):
                audit.record(
                    _actor(request), "view", target=request.url.path,
                    details={"method": request.method, "status": response.status_code},
                )
        except Exception:  # pragma: no cover - logging must never break a request
            pass
        return response


def client_ip_from_scope(scope) -> str | None:
    """Resolve the real client IP from a request scope, accounting for proxy
    layers. Trust order: Cloudflare (CF-Connecting-IP / True-Client-IP), then the
    left-most X-Forwarded-For (original client through nginx/Traefik hops), then
    X-Real-IP, then the direct peer. The app listens only on loopback behind our
    own reverse proxy, so these headers are set by us and can be trusted."""
    headers: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        headers[k.decode("latin-1").lower()] = v.decode("latin-1")
    for h in ("cf-connecting-ip", "true-client-ip"):
        if headers.get(h):
            return headers[h].strip()
    xff = headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if headers.get("x-real-ip"):
        return headers["x-real-ip"].strip()
    client = scope.get("client")
    return client[0] if client else None


class ClientIPMiddleware:
    """Pure-ASGI middleware that stashes the proxy-resolved client IP in a
    ContextVar (app.audit) so every audit entry records it — both the activity
    middleware and the route-level hooks. Registered outermost so the value is
    set before any handler runs; pure-ASGI (not BaseHTTPMiddleware) so the
    ContextVar propagates cleanly into the downstream request context."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            audit.set_client_ip(client_ip_from_scope(scope))
        await self.app(scope, receive, send)
