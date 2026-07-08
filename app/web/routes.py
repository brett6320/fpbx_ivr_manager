"""HTTP routes: auth flow + schedule management UI/API."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import authz, backend, entra, local
from app.auth.authz import MANAGE_SCHEDULES
from app.config import settings
from app.fpbx.time_conditions import NotManaged
from app.mfa import totp
from app.models import ScheduleRequest
from app.service import (
    apply_schedule,
    delete_schedule,
    get_schedule,
    list_schedules,
    preview_phrase,
)

# every schedule route requires the manage_schedules permission (granted via groups)
require_schedules = authz.require(MANAGE_SCHEDULES)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ---- auth ----
def _login_page(request: Request, error: str | None = None, status: int = 200):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"sso": backend.is_sso(), "org": settings.app_org_name, "error": error,
         "show_local_admin": backend.kind() != "local"},
        status_code=status,
    )


def _post_password(request: Request, user: dict):
    """After a valid password: enforce MFA for local admins, else complete login.

    is_admin is only set by the local backend, so MFA here applies specifically to
    LOCAL admins — external IdP users satisfy MFA at the IdP.
    """
    if user.get("is_admin"):
        stage = "verify" if local.has_mfa(user["email"]) else "enroll"
        request.session["mfa"] = {"user": user, "stage": stage}
        return RedirectResponse("/auth/mfa", status_code=303)
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


def _finish_mfa(request: Request):
    ctx = request.session.pop("mfa", None)
    request.session.pop("mfa_totp_pending", None)
    request.session.pop("webauthn_challenge", None)
    if not ctx:
        return None
    request.session["user"] = ctx["user"]
    return ctx["user"]


@router.get("/auth/login")
def login(request: Request):
    if backend.is_sso():
        state = secrets.token_urlsafe(16)
        request.session["oauth_state"] = state
        return RedirectResponse(entra.auth_url(state))
    return _login_page(request)


@router.post("/auth/login")
async def login_submit(request: Request):
    if backend.is_sso():
        return RedirectResponse("/auth/login", status_code=303)
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    user = backend.password_login(username, password)
    if not user:
        return _login_page(request, error="Invalid username or password", status=401)
    return _post_password(request, user)


# ---- break-glass: local admin sign-in, ALWAYS available (even under external IdP) ----
@router.get("/auth/local")
def local_login(request: Request, error: str | None = None):
    return templates.TemplateResponse(
        request, "local_login.html", {"org": settings.app_org_name, "error": error}
    )


@router.post("/auth/local")
async def local_login_submit(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    user = local.authenticate(username, password)
    # break-glass is restricted to local administrators
    if not user or not user.get("is_admin"):
        return templates.TemplateResponse(
            request,
            "local_login.html",
            {"org": settings.app_org_name,
             "error": "Invalid credentials, or account is not a local administrator."},
            status_code=401,
        )
    return _post_password(request, user)


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not code or state != request.session.get("oauth_state"):
        return HTMLResponse("Invalid auth state", status_code=400)
    result = entra.redeem_code(code)
    user = entra.user_from_claims(result["id_token_claims"])
    request.session["user"] = user
    return RedirectResponse("/", status_code=303)


@router.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---- MFA (local admins) ----
def _mfa_ctx(request: Request) -> dict | None:
    return request.session.get("mfa")


def _mfa_username(request: Request) -> str | None:
    ctx = _mfa_ctx(request)
    return ctx["user"]["email"] if ctx else None


@router.get("/auth/mfa")
def mfa_home(request: Request):
    ctx = _mfa_ctx(request)
    if not ctx:
        return RedirectResponse("/auth/login", status_code=307)
    if ctx["stage"] == "enroll":
        return RedirectResponse("/auth/mfa/setup", status_code=303)
    username = ctx["user"]["email"]
    return templates.TemplateResponse(
        request,
        "mfa_verify.html",
        {
            "org": settings.app_org_name,
            "has_totp": bool(local.get_totp_secret(username)),
            "has_passkey": bool(local.get_credentials(username)),
            "error": None,
        },
    )


@router.get("/auth/mfa/setup")
def mfa_setup(request: Request):
    ctx = _mfa_ctx(request)
    if not ctx:
        return RedirectResponse("/auth/login", status_code=307)
    username = ctx["user"]["email"]
    secret = request.session.get("mfa_totp_pending")
    if not secret:
        secret = totp.generate_secret()
        request.session["mfa_totp_pending"] = secret
    uri = totp.provisioning_uri(secret, account=username, issuer=settings.mfa_issuer)
    return templates.TemplateResponse(
        request,
        "mfa_setup.html",
        {"org": settings.app_org_name, "secret": secret, "otpauth_uri": uri, "error": None},
    )


@router.post("/auth/mfa/totp/enroll")
async def mfa_totp_enroll(request: Request):
    ctx = _mfa_ctx(request)
    if not ctx:
        return RedirectResponse("/auth/login", status_code=307)
    secret = request.session.get("mfa_totp_pending")
    form = await request.form()
    code = form.get("code") or ""
    if not secret or not totp.verify(secret, code):
        uri = totp.provisioning_uri(secret or "", ctx["user"]["email"], settings.mfa_issuer)
        return templates.TemplateResponse(
            request,
            "mfa_setup.html",
            {"org": settings.app_org_name, "secret": secret, "otpauth_uri": uri,
             "error": "Code did not match. Try again."},
            status_code=400,
        )
    local.set_totp_secret(ctx["user"]["email"], secret)
    _finish_mfa(request)
    return RedirectResponse("/", status_code=303)


@router.post("/auth/mfa/totp/verify")
async def mfa_totp_verify(request: Request):
    ctx = _mfa_ctx(request)
    if not ctx:
        return RedirectResponse("/auth/login", status_code=307)
    username = ctx["user"]["email"]
    form = await request.form()
    code = form.get("code") or ""
    if not totp.verify(local.get_totp_secret(username) or "", code):
        return templates.TemplateResponse(
            request,
            "mfa_verify.html",
            {"org": settings.app_org_name,
             "has_totp": True, "has_passkey": bool(local.get_credentials(username)),
             "error": "Invalid authentication code."},
            status_code=401,
        )
    _finish_mfa(request)
    return RedirectResponse("/", status_code=303)


# ---- MFA: passkey (WebAuthn) JSON endpoints ----
def _passkey():
    from app.mfa import passkey  # optional dep

    return passkey


@router.post("/auth/mfa/passkey/register-options")
def passkey_register_options(request: Request):
    ctx = _mfa_ctx(request)
    if not ctx:
        return JSONResponse({"error": "no pending mfa"}, status_code=403)
    options, challenge = _passkey().registration_options(
        ctx["user"]["email"], ctx["user"].get("name") or ctx["user"]["email"]
    )
    request.session["webauthn_challenge"] = challenge
    return HTMLResponse(options, media_type="application/json")


@router.post("/auth/mfa/passkey/register")
async def passkey_register(request: Request):
    ctx = _mfa_ctx(request)
    challenge = request.session.get("webauthn_challenge")
    if not ctx or not challenge:
        return JSONResponse({"error": "no pending registration"}, status_code=403)
    body = (await request.body()).decode()
    try:
        _passkey().verify_registration(ctx["user"]["email"], body, challenge, label="passkey")
    except Exception as e:  # noqa: BLE001 - surface verification failure to client
        return JSONResponse({"error": f"registration failed: {e}"}, status_code=400)
    _finish_mfa(request)
    return JSONResponse({"ok": True, "redirect": "/"})


@router.post("/auth/mfa/passkey/auth-options")
def passkey_auth_options(request: Request):
    ctx = _mfa_ctx(request)
    if not ctx:
        return JSONResponse({"error": "no pending mfa"}, status_code=403)
    options, challenge = _passkey().authentication_options(ctx["user"]["email"])
    request.session["webauthn_challenge"] = challenge
    return HTMLResponse(options, media_type="application/json")


@router.post("/auth/mfa/passkey/auth")
async def passkey_auth(request: Request):
    ctx = _mfa_ctx(request)
    challenge = request.session.get("webauthn_challenge")
    if not ctx or not challenge:
        return JSONResponse({"error": "no pending assertion"}, status_code=403)
    body = (await request.body()).decode()
    if not _passkey().verify_authentication(ctx["user"]["email"], body, challenge):
        return JSONResponse({"error": "assertion failed"}, status_code=401)
    _finish_mfa(request)
    return JSONResponse({"ok": True, "redirect": "/"})


# ---- UI ----
def _form_ctx(user: dict, c: dict | None = None) -> dict:
    return {
        "user": user,
        "org": settings.app_org_name,
        "pool": f"{settings.ext_pool_start}-{settings.ext_pool_end}",
        "c": c,  # prefill dict for edit mode, else None
    }


@router.get("/", response_class=HTMLResponse)
def index(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(
        request,
        "schedules.html",
        {"user": user, "schedules": list_schedules()},
    )


@router.get("/schedules/new", response_class=HTMLResponse)
def new_schedule(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(request, "schedule_form.html", _form_ctx(user))


@router.get("/schedules/{ext}/edit", response_class=HTMLResponse)
def edit_schedule(request: Request, ext: int, user: dict = Depends(require_schedules)):
    c = get_schedule(ext)
    if not c:
        return HTMLResponse("schedule not found", status_code=404)
    return templates.TemplateResponse(request, "schedule_form.html", _form_ctx(user, c))


@router.post("/schedules/{ext}/delete")
def remove_schedule(request: Request, ext: int, user: dict = Depends(require_schedules)):
    try:
        delete_schedule(ext)
    except NotManaged as e:
        return HTMLResponse(f"Refused: {e}", status_code=409)
    return RedirectResponse("/", status_code=303)


def _parse(form) -> ScheduleRequest:
    return ScheduleRequest(
        label=form["label"],
        start=form["start"],
        end=form["end"],
        reason=form.get("reason") or None,
        extension=int(form["extension"]) if form.get("extension") else None,
        closed_action=form.get("closed_action", "voicemail"),
    )


@router.post("/preview", response_class=HTMLResponse)
async def preview(request: Request, user: dict = Depends(require_schedules)):
    form = await request.form()
    text = preview_phrase(_parse(form))
    return HTMLResponse(f'<div class="preview">{text}</div>')


@router.post("/apply", response_class=HTMLResponse)
async def apply(request: Request, user: dict = Depends(require_schedules)):
    form = await request.form()
    req = _parse(form)
    open_dest = form.get("open_destination", "").strip()
    if not open_dest:
        return HTMLResponse("open_destination is required", status_code=400)
    try:
        result = apply_schedule(req, open_dest)
    except NotManaged as e:
        return HTMLResponse(f"Refused: {e}", status_code=409)
    return templates.TemplateResponse(request, "result.html", {"r": result})
