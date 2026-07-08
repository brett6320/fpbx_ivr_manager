"""HTTP routes: auth flow + schedule management UI/API."""
from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import business
from app.auth import authz, backend, config_store, entra, local, probe
from app.auth.authz import MANAGE_SCHEDULES, MANAGE_USERS
from app.config import settings
from app.fpbx import destinations
from app.fpbx.time_conditions import NotManaged
from app.mfa import totp
from app.models import Closure, IvrOption, IvrRequest, ScheduleRequest, TimeConditionRequest
from app.phrases import builder
from app.service import (
    adopt_schedule,
    apply_time_condition,
    build_call_flow,
    create_ivr,
    delete_ivr,
    delete_schedule,
    get_adoptable,
    get_schedule,
    list_adoptable,
    list_destinations,
    list_inbound_destinations,
    list_ivrs,
    list_recordings,
    list_recycled,
    list_schedules,
    preview_phrase,
    recycle_ivr,
)

# every schedule route requires the manage_schedules permission (granted via groups)
require_schedules = authz.require(MANAGE_SCHEDULES)
# auth administration requires the manage_users permission
require_users = authz.require(MANAGE_USERS)

log = logging.getLogger("fpbx_ivr_manager")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# used by the shared top nav to show admin-only links
templates.env.globals["nav_can_admin"] = (
    lambda u: bool(u) and authz.has_permission(u, MANAGE_USERS)
)
templates.env.globals["nav_local_backend"] = lambda: backend.kind() == "local"


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
    LOCAL admins — external IdP users satisfy MFA at the IdP. In dev mode MFA is
    not enforced (no forced enrollment).
    """
    if user.get("is_admin") and not settings.dev_mode:
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
        raise HTTPException(status_code=400, detail="Invalid authentication state. Please try signing in again.")
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
    except Exception:  # noqa: BLE001 - verification failure; detail to logs, not client
        log.warning("passkey registration failed", exc_info=True)
        return JSONResponse({"error": "registration failed"}, status_code=400)
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
def _form_ctx(user: dict, c: dict | None = None, adopt_uuid: str | None = None) -> dict:
    return {
        "user": user,
        "org": settings.app_org_name,
        "pool": f"{settings.ext_pool_start}-{settings.ext_pool_end}",
        "c": c,  # prefill dict for edit mode, else None
        "adopt_uuid": adopt_uuid,  # set => adoption mode (posts to /admin/adopt)
        "placeholders": business.placeholder_keys(),
    }


@router.get("/", response_class=HTMLResponse)
def index(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(
        request,
        "schedules.html",
        {
            "user": user,
            "schedules": list_schedules(),
            "can_admin": authz.has_permission(user, MANAGE_USERS),
        },
    )


@router.get("/schedules/new", response_class=HTMLResponse)
def new_schedule(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(request, "schedule_form.html", _form_ctx(user))


@router.get("/schedules/{ext}/edit", response_class=HTMLResponse)
def edit_schedule(request: Request, ext: int, user: dict = Depends(require_schedules)):
    c = get_schedule(ext)
    if not c:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return templates.TemplateResponse(request, "schedule_form.html", _form_ctx(user, c))


async def _require_delete_confirm(request: Request) -> None:
    """Deletion of any resource requires an explicit affirmative confirmation
    token in the request body; a request without it is refused server-side (not
    just via the browser prompt)."""
    form = await request.form()
    if (form.get("confirm") or "").strip().lower() != "yes":
        raise HTTPException(
            status_code=400,
            detail="Deletion requires confirmation. Retry from the app and confirm the prompt.",
        )


@router.post("/schedules/{ext}/delete")
async def remove_schedule(request: Request, ext: int, user: dict = Depends(require_schedules)):
    await _require_delete_confirm(request)
    try:
        delete_schedule(ext)
    except NotManaged:
        log.warning("guardrail refused operation", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail="Refused: the target extension or dialplan was not created by this app "
            "(see server logs for details).",
        ) from None
    return RedirectResponse("/", status_code=303)


def _parse(form) -> ScheduleRequest:
    """A single closure — used for greeting preview."""
    return ScheduleRequest(
        label=form.get("label") or "closure",
        start=form["start"],
        end=form["end"],
        reason=form.get("reason") or None,
        closed_action=form.get("closed_action", "voicemail"),
    )


def _parse_closures(form) -> list[Closure]:
    """Closure rows arrive as parallel c_label_<i> / c_start_<i> / … fields."""
    closures = []
    for key in form:
        if not key.startswith("c_label_"):
            continue
        i = key[len("c_label_"):]
        label = (form.get(key) or "").strip()
        start, end = form.get(f"c_start_{i}"), form.get(f"c_end_{i}")
        if not (label and start and end):
            continue
        closures.append(Closure(
            label=label, start=start, end=end,
            reason=(form.get(f"c_reason_{i}") or None),
            closed_action=form.get(f"c_action_{i}", "voicemail"),
        ))
    return closures


def _parse_tc(form) -> TimeConditionRequest:
    return TimeConditionRequest(
        name=(form.get("name") or "").strip(),
        open_destination=(form.get("open_destination") or "").strip(),
        extension=int(form["extension"]) if form.get("extension") else None,
        closures=_parse_closures(form),
    )


@router.post("/preview", response_class=HTMLResponse)
async def preview(request: Request, user: dict = Depends(require_schedules)):
    form = await request.form()
    text = preview_phrase(_parse(form))
    return HTMLResponse(f'<div class="preview">{text}</div>')


@router.post("/apply", response_class=HTMLResponse)
async def apply(request: Request, user: dict = Depends(require_schedules)):
    form = await request.form()
    if not (form.get("open_destination") or "").strip():
        raise HTTPException(status_code=400, detail="A normal daytime (open) destination is required.")
    try:
        result = apply_time_condition(_parse_tc(form))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except NotManaged:
        log.warning("guardrail refused operation", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail="Refused: the target extension or dialplan was not created by this app "
            "(see server logs for details).",
        ) from None
    return templates.TemplateResponse(request, "result.html", {"r": result})


# ---- interactive auth administration (SSO / LDAP enablement + live testing) ----
@router.get("/admin/auth", response_class=HTMLResponse)
def admin_auth(request: Request, user: dict = Depends(require_users)):
    # prefill non-secret fields from current settings; secrets are never sent out
    ctx = {
        "user": user,
        "org": settings.app_org_name,
        "backend": backend.kind(),
        "redirect_uri": settings.redirect_uri,
        "authz": settings.authz_group_permissions,
        "entra": {
            "tenant_id": settings.entra_tenant_id,
            "client_id": settings.entra_client_id,
            "has_secret": bool(settings.entra_client_secret),
        },
        "ldap": {
            "uri": settings.ldap_uri,
            "bind_dn_template": settings.ldap_bind_dn_template,
            "base_dn": settings.ldap_base_dn,
            "user_filter": settings.ldap_user_filter,
            "group_base_dn": settings.ldap_group_base_dn,
            "group_filter": settings.ldap_group_filter,
            "start_tls": settings.ldap_start_tls,
        },
    }
    return templates.TemplateResponse(request, "admin_auth.html", ctx)


@router.post("/admin/auth/test/ldap")
async def admin_test_ldap(request: Request, user: dict = Depends(require_users)):
    b = await request.json()
    result = probe.ldap_probe(
        uri=b.get("uri", ""),
        bind_dn_template=b.get("bind_dn_template", ""),
        base_dn=b.get("base_dn", ""),
        user_filter=b.get("user_filter", "(uid={username})"),
        group_base_dn=b.get("group_base_dn", ""),
        group_filter=b.get("group_filter", "(member={user_dn})"),
        start_tls=bool(b.get("start_tls", True)),
        test_user=b.get("test_user", ""),
        test_password=b.get("test_password", ""),
        group_permissions=b.get("group_permissions", "{}"),
    )
    return JSONResponse(result)


@router.post("/admin/auth/test/entra")
async def admin_test_entra(request: Request, user: dict = Depends(require_users)):
    b = await request.json()
    result = probe.entra_probe(
        tenant_id=b.get("tenant_id", ""),
        client_id=b.get("client_id", ""),
        client_secret=b.get("client_secret", ""),
        redirect_uri=settings.redirect_uri,
    )
    return JSONResponse(result)


@router.post("/admin/auth/test/token")
async def admin_test_token(request: Request, user: dict = Depends(require_users)):
    b = await request.json()
    return JSONResponse(
        probe.entra_decode_token(b.get("id_token", ""), b.get("group_permissions", "{}"))
    )


@router.post("/admin/auth/save")
async def admin_save(request: Request, user: dict = Depends(require_users)):
    b = await request.json()
    # only whitelisted keys are accepted by config_store.write_managed
    updates = {k: str(v) for k, v in b.items() if k in config_store.MANAGED_KEYS}
    # never persist an empty secret over an existing one: drop blank secrets
    for sk in config_store.SECRET_KEYS:
        if sk in updates and updates[sk] == "":
            updates.pop(sk)
    try:
        written = config_store.write_managed(updates)
    except ValueError:
        log.warning("rejected non-managed auth config keys", exc_info=True)
        return JSONResponse(
            {"ok": False, "error": "one or more keys are not permitted"}, status_code=400
        )
    return JSONResponse(
        {"ok": True, "written": written, "restart_required": True,
         "note": "Saved. Restart the service to apply the new auth configuration."}
    )


# ---- manual adoption of existing FusionPBX time conditions (admins only) ----
@router.get("/admin/adopt", response_class=HTMLResponse)
def admin_adopt_list(request: Request, user: dict = Depends(require_users)):
    return templates.TemplateResponse(
        request, "adopt.html", {"user": user, "candidates": list_adoptable()}
    )


@router.get("/admin/adopt/{dialplan_uuid}", response_class=HTMLResponse)
def admin_adopt_form(request: Request, dialplan_uuid: str, user: dict = Depends(require_users)):
    try:
        tc = get_adoptable(dialplan_uuid)
    except NotManaged:
        raise HTTPException(status_code=409, detail="This time condition cannot be adopted (it may already be managed by this app).") from None
    c = {
        "extension": tc["extension"],
        "label": tc["description"] or tc["name"],
        "start": None,
        "end": None,
        "open_destination": None,
        "closed_action": "voicemail",
    }
    return templates.TemplateResponse(
        request, "schedule_form.html", _form_ctx(user, c, adopt_uuid=dialplan_uuid)
    )


@router.post("/admin/adopt", response_class=HTMLResponse)
async def admin_adopt_apply(request: Request, user: dict = Depends(require_users)):
    form = await request.form()
    adopt_uuid = (form.get("adopt_uuid") or "").strip()
    if not adopt_uuid:
        raise HTTPException(status_code=400, detail="No time condition was selected to adopt.")
    open_dest = (form.get("open_destination") or "").strip()
    if not open_dest:
        raise HTTPException(status_code=400, detail="A normal daytime (open) destination is required.")
    closures = _parse_closures(form)
    if not closures:
        raise HTTPException(status_code=400, detail="A closure window is required.")
    try:
        result = adopt_schedule(adopt_uuid, closures[0], open_dest)
    except NotManaged:
        log.warning("adopt refused", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail="Refused: the selected time condition is not adoptable (see server logs).",
        ) from None
    return templates.TemplateResponse(request, "result.html", {"r": result})


# ---- FusionPBX version/schema compatibility (admins) ----
@router.get("/admin/compat", response_class=HTMLResponse)
def admin_compat(request: Request, user: dict = Depends(require_users)):
    from app.fpbx import compat

    try:
        result = compat.check_schema()
        error = None
    except Exception:  # noqa: BLE001 - report DB reachability to the admin, not a trace
        log.warning("schema check failed", exc_info=True)
        result = {"ok": False, "supported": compat.SUPPORTED_RANGE,
                  "checked_tables": [], "missing_tables": [], "missing_columns": []}
        error = "Could not query the database."
    return templates.TemplateResponse(
        request, "compat.html", {"user": user, "r": result, "error": error}
    )


# ---- IVR menus ----
_DIGITS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "#"]


@router.get("/ivrs", response_class=HTMLResponse)
def ivrs_list(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(
        request, "ivrs.html",
        {"user": user, "ivrs": list_ivrs(), "recycled": list_recycled()},
    )


@router.get("/ivrs/new", response_class=HTMLResponse)
def ivr_new(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(
        request,
        "ivr_form.html",
        {
            "user": user,
            "org": settings.app_org_name,
            "pool": f"{settings.ext_pool_start}-{settings.ext_pool_end}",
            "digits": _DIGITS,
            "destinations": list_destinations(),
            "recordings": list_recordings(),
            "placeholders": business.placeholder_keys(),
        },
    )


def _parse_ivr(form) -> IvrRequest:
    options = []
    for d in _DIGITS:
        dest = (form.get(f"opt_{d}") or "").strip()
        if dest:
            options.append(IvrOption(digits=d, destination=dest))

    manual = (form.get("timeout_manual") or "").strip()
    timeout = destinations.manual_destination(manual)["value"] if manual else (form.get("timeout_dest") or "").strip()

    mode = form.get("greeting_mode", "tts")
    return IvrRequest(
        name=form["name"],
        extension=int(form["extension"]) if form.get("extension") else None,
        greeting_text=(form.get("greeting_text") or None) if mode == "tts" else None,
        greeting_recording=(form.get("greeting_recording") or None) if mode == "recording" else None,
        timeout_destination=timeout,
        options=options,
    )


@router.post("/ivrs", response_class=HTMLResponse)
async def ivr_create(request: Request, user: dict = Depends(require_schedules)):
    form = await request.form()
    try:
        req = _parse_ivr(form)
        result = create_ivr(req)
    except (ValueError, NotManaged):
        log.warning("IVR create rejected", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Could not create IVR: check the greeting, options, timeout, and extension "
            "(see server logs for details).",
        ) from None
    return templates.TemplateResponse(request, "ivr_result.html", {"r": result})


@router.post("/ivrs/{ext}/delete")
async def ivr_remove(request: Request, ext: int, user: dict = Depends(require_schedules)):
    await _require_delete_confirm(request)
    try:
        delete_ivr(ext)
    except NotManaged:
        log.warning("ivr delete refused", exc_info=True)
        raise HTTPException(status_code=409, detail="Refused: that IVR was not created by this app.") from None
    return RedirectResponse("/ivrs", status_code=303)


@router.post("/ivrs/{ext}/recycle")
async def ivr_recycle(request: Request, ext: int, user: dict = Depends(require_schedules)):
    await _require_delete_confirm(request)
    actor = user.get("email") or user.get("name") or "unknown"
    try:
        recycle_ivr(ext, actor=actor)
    except NotManaged:
        log.warning("ivr recycle refused", exc_info=True)
        raise HTTPException(status_code=409, detail="Refused: that IVR was not created by this app.") from None
    return RedirectResponse("/ivrs", status_code=303)


# ---- one-flow call-flow wizard (inbound route -> time condition -> IVR) ----
@router.get("/flow/new", response_class=HTMLResponse)
def flow_new(request: Request, user: dict = Depends(require_schedules)):
    return templates.TemplateResponse(
        request,
        "flow_form.html",
        {
            "user": user,
            "org": settings.app_org_name,
            "pool": f"{settings.ext_pool_start}-{settings.ext_pool_end}",
            "digits": _DIGITS,
            "destinations": list_destinations(),
            "inbound_destinations": list_inbound_destinations(),
            "recordings": list_recordings(),
            "placeholders": business.placeholder_keys(),
        },
    )


@router.post("/flow", response_class=HTMLResponse)
async def flow_create(request: Request, user: dict = Depends(require_schedules)):
    form = await request.form()
    did = (form.get("inbound_number") or "").strip()
    confirm = bool(form.get("confirm_overwrite"))
    try:
        schedule_req = _parse(form)     # label/start/end/reason/closed_action
        ivr_req = _parse_ivr(form)      # name/greeting/options/timeout
        result = build_call_flow(did or None, schedule_req, ivr_req, confirm_overwrite=confirm)
    except NotManaged:
        log.warning("call flow refused (inbound overwrite)", exc_info=True)
        raise HTTPException(
            status_code=409,
            detail="The selected inbound DID already has a route that this app did not "
            "create. Nothing was changed. Tick “replace the existing inbound route” to "
            "overwrite it, or choose a different DID.",
        ) from None
    except ValueError:
        log.warning("call flow build rejected", exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Could not build call flow: check the schedule window and IVR fields "
            "(see server logs for details).",
        ) from None
    return templates.TemplateResponse(request, "flow_result.html", {"r": result})


# ---- business profile: name + reusable named templates (admins) ----
@router.get("/admin/business", response_class=HTMLResponse)
def admin_business(request: Request, user: dict = Depends(require_users), saved: int = 0):
    return templates.TemplateResponse(
        request,
        "business.html",
        {
            "user": user,
            "business_name": business.load().get("business_name", ""),
            "org_fallback": settings.app_org_name,
            "rows": list(business.templates().items()),  # existing templates
            "closure_opening": business.closure_opening(),
            "closure_closing": business.closure_closing(),
            "default_opening": builder.DEFAULT_OPENING,
            "default_closing": builder.DEFAULT_CLOSING,
            "placeholders": business.placeholder_keys(),
            "saved": bool(saved),
        },
    )


@router.post("/admin/business")
async def admin_business_save(request: Request, user: dict = Depends(require_users)):
    form = await request.form()
    name = (form.get("business_name") or "").strip()
    # rows are added/removed dynamically, so indices may be sparse — pair each
    # tmpl_name_<suffix> with its tmpl_value_<suffix>.
    tmpls: dict[str, str] = {}
    for key in form:
        if not key.startswith("tmpl_name_"):
            continue
        suffix = key[len("tmpl_name_"):]
        tname = (form.get(key) or "").strip()
        if tname:
            tmpls[tname] = (form.get(f"tmpl_value_{suffix}") or "").strip()
    business.save(
        name, tmpls,
        closure_opening=(form.get("closure_opening") or "").strip(),
        closure_closing=(form.get("closure_closing") or "").strip(),
    )
    return RedirectResponse("/admin/business?saved=1", status_code=303)


# ---- local user management (admins; only when AUTH_BACKEND=local) ----
def _require_local() -> None:
    if backend.kind() != "local":
        raise HTTPException(
            status_code=404,
            detail="User management is only available with the local auth backend.",
        )


def _known_groups() -> list[str]:
    import json
    try:
        perms = json.loads(settings.authz_group_permissions or "{}")
    except ValueError:
        perms = {}
    groups = set(perms)
    if settings.local_admin_group:
        groups.add(settings.local_admin_group)
    return sorted(groups)


@router.get("/admin/users", response_class=HTMLResponse)
def users_list(request: Request, user: dict = Depends(require_users)):
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "user": user,
            "is_local": backend.kind() == "local",
            "users": local.list_users_detailed() if backend.kind() == "local" else [],
        },
    )


def _user_form(request: Request, user: dict, u: dict | None):
    return templates.TemplateResponse(
        request, "user_form.html",
        {"user": user, "u": u, "known_groups": _known_groups(),
         "admin_group": settings.local_admin_group},
    )


@router.get("/admin/users/new", response_class=HTMLResponse)
def user_new(request: Request, user: dict = Depends(require_users)):
    _require_local()
    return _user_form(request, user, None)


@router.get("/admin/users/{username}/edit", response_class=HTMLResponse)
def user_edit(request: Request, username: str, user: dict = Depends(require_users)):
    _require_local()
    u = local.get_user(username)
    if not u:
        raise HTTPException(status_code=404, detail="User not found.")
    return _user_form(request, user, u)


@router.post("/admin/users")
async def user_create(request: Request, user: dict = Depends(require_users)):
    _require_local()
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required.")
    if local.user_exists(username):
        raise HTTPException(status_code=409, detail=f"User {username!r} already exists.")
    local.create_user(username, password, (form.get("display_name") or "").strip() or username,
                      is_admin=bool(form.get("is_admin")))
    local.set_groups(username, (form.get("groups") or "").split(","))
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{username}")
async def user_update(request: Request, username: str, user: dict = Depends(require_users)):
    _require_local()
    if not local.user_exists(username):
        raise HTTPException(status_code=404, detail="User not found.")
    form = await request.form()
    display = (form.get("display_name") or "").strip() or username
    with_pw = form.get("password") or ""
    local.set_admin(username, bool(form.get("is_admin")))
    local.set_groups(username, (form.get("groups") or "").split(","))
    local.set_display_name(username, display)
    if with_pw:
        local.set_password(username, with_pw)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{username}/mfa-reset")
def user_mfa_reset(request: Request, username: str, user: dict = Depends(require_users)):
    _require_local()
    local.reset_mfa(username)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{username}/delete")
async def user_delete(request: Request, username: str, user: dict = Depends(require_users)):
    _require_local()
    await _require_delete_confirm(request)
    if username == user.get("email"):
        raise HTTPException(status_code=400, detail="You cannot delete the account you are signed in as.")
    local.delete_user(username)
    return RedirectResponse("/admin/users", status_code=303)


# ---- export / import of managed items (admins) ----
@router.get("/admin/export")
def admin_export(request: Request, user: dict = Depends(require_users)):
    from app import portability

    return JSONResponse(
        portability.export_document(),
        headers={"Content-Disposition": 'attachment; filename="fpbx-ivr-manager-export.json"'},
    )


@router.get("/admin/import", response_class=HTMLResponse)
def admin_import(request: Request, user: dict = Depends(require_users)):
    return templates.TemplateResponse(request, "import_upload.html", {"user": user})


@router.post("/admin/import", response_class=HTMLResponse)
async def admin_import_review(request: Request, user: dict = Depends(require_users)):
    import json

    from app import portability

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Choose an export file to import.")
    try:
        doc = json.loads((await upload.read()).decode())
        items = portability.to_review_items(doc)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="That file is not a valid export document.") from None
    if not items:
        raise HTTPException(status_code=400, detail="No importable items found in that file.")
    # pretty JSON per item for the editable review textareas
    rows = [{"type": it["type"], "label": it["label"],
             "json": json.dumps(it["data"], indent=2)} for it in items]
    return templates.TemplateResponse(request, "import_review.html", {"user": user, "rows": rows})


@router.post("/admin/import/commit", response_class=HTMLResponse)
async def admin_import_commit(request: Request, user: dict = Depends(require_users)):
    import json

    from app import portability

    form = await request.form()
    count = int(form.get("count") or 0)
    results = []
    for i in range(count):
        if not form.get(f"include_{i}"):
            continue
        item_type = form.get(f"type_{i}") or ""
        label = form.get(f"label_{i}") or item_type
        try:
            data = json.loads(form.get(f"data_{i}") or "{}")
            msg = portability.commit_item(item_type, data)
            results.append({"label": label, "ok": True, "message": msg})
        except Exception:  # noqa: BLE001 - report per-item, keep going
            # log the item index (not the user-supplied label) to avoid log injection
            log.warning("import commit failed for item %d", i, exc_info=True)
            results.append({"label": label, "ok": False,
                            "message": "failed — check the item's fields (see server logs)"})
    return templates.TemplateResponse(request, "import_result.html", {"user": user, "results": results})
