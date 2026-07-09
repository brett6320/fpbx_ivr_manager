"""Application settings loaded from environment / .env."""
from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# App-writable override file for auth config managed via the admin UI. Lives in
# the app's state dir (not the root-owned main env), so persisting from the web
# UI never requires loosening the main env file's permissions. Loaded after
# .env, so it overrides .env values (real process env vars still win over both).
AUTH_CONFIG_FILE = os.environ.get("AUTH_CONFIG_FILE", "data/auth.env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", AUTH_CONFIG_FILE), extra="ignore")

    # web
    app_secret_key: str = Field(alias="APP_SECRET_KEY")
    app_base_url: str = Field(alias="APP_BASE_URL")
    # Sub-path the app is served under, e.g. "/ivr-manager". Empty = served at the
    # site root. When unset, it is derived from the path of APP_BASE_URL so a single
    # setting covers both the external URL and internal link generation.
    base_path_raw: str = Field(alias="BASE_PATH", default="")
    app_org_name: str = Field(alias="APP_ORG_NAME", default="Our office")
    # App-writable JSON store for the business profile (name + hours templates),
    # managed via the admin UI. Lives in the app's state dir.
    business_config_file: str = Field(alias="BUSINESS_CONFIG_FILE", default="data/business.json")
    # App-writable JSON ledger recording extensions recycled (freed) from prior
    # IVR use, so the previous use is logged before the number is reused.
    recycle_log_file: str = Field(alias="RECYCLE_LOG_FILE", default="data/recycled.json")
    # Dev mode relaxes security for local development (e.g. does NOT force MFA
    # enrollment for local admins). Never enable in production.
    dev_mode: bool = Field(alias="DEV_MODE", default=False)
    # Mark the session cookie Secure? Empty = derive from APP_BASE_URL scheme
    # (https -> Secure). Set false to allow login over plain HTTP.
    session_https_only_raw: str = Field(alias="SESSION_HTTPS_ONLY", default="")

    # auth backend: local (default) | entra | ldap | fpbx
    auth_backend: str = Field(alias="AUTH_BACKEND", default="local")

    # local auth (default backend)
    local_auth_db: str = Field(alias="LOCAL_AUTH_DB", default="data/users.db")
    # Hash-chained, admin-only audit log (see app.audit). Lives in the state dir.
    audit_db: str = Field(alias="AUDIT_DB", default="data/audit.db")
    local_admin_user: str = Field(alias="LOCAL_ADMIN_USER", default="")
    local_admin_password: str = Field(alias="LOCAL_ADMIN_PASSWORD", default="")
    local_admin_group: str = Field(alias="LOCAL_ADMIN_GROUP", default="ivr-admins")

    # entra (only required when AUTH_BACKEND=entra)
    entra_tenant_id: str = Field(alias="ENTRA_TENANT_ID", default="")
    entra_client_id: str = Field(alias="ENTRA_CLIENT_ID", default="")
    entra_client_secret: str = Field(alias="ENTRA_CLIENT_SECRET", default="")
    entra_allowed_group_id: str = Field(alias="ENTRA_ALLOWED_GROUP_ID", default="")

    # ldap (only required when AUTH_BACKEND=ldap)
    ldap_uri: str = Field(alias="LDAP_URI", default="")
    ldap_bind_dn_template: str = Field(alias="LDAP_BIND_DN_TEMPLATE", default="")
    ldap_base_dn: str = Field(alias="LDAP_BASE_DN", default="")
    ldap_user_filter: str = Field(alias="LDAP_USER_FILTER", default="(uid={username})")
    ldap_group_filter: str = Field(
        alias="LDAP_GROUP_FILTER", default="(member={user_dn})"
    )
    ldap_group_base_dn: str = Field(alias="LDAP_GROUP_BASE_DN", default="")
    ldap_start_tls: bool = Field(alias="LDAP_START_TLS", default=True)

    # ---- Authorization: permissions are granted to GROUPS only, never to users.
    # JSON mapping of group identifier -> list of permissions. The group identifier
    # is: the group name (local), the group object-id or name in the token 'groups'
    # claim (entra), or the group cn/dn (ldap).
    # e.g. {"ivr-admins": ["manage_schedules","manage_users"], "ivr-editors": ["manage_schedules"]}
    authz_group_permissions: str = Field(alias="AUTHZ_GROUP_PERMISSIONS", default="{}")

    # fusionpbx db
    fpbx_db_host: str = Field(alias="FPBX_DB_HOST")
    fpbx_db_port: int = Field(alias="FPBX_DB_PORT", default=5432)
    fpbx_db_name: str = Field(alias="FPBX_DB_NAME", default="fusionpbx")
    fpbx_db_user: str = Field(alias="FPBX_DB_USER")
    fpbx_db_password: str = Field(alias="FPBX_DB_PASSWORD")
    fpbx_domain_name: str = Field(alias="FPBX_DOMAIN_NAME")

    # startup schema-capability check: strict (fail boot) | warn (log) | off
    fpbx_schema_check: str = Field(alias="FPBX_SCHEMA_CHECK", default="warn")

    # freeswitch xmlrpc
    fs_xmlrpc_url: str = Field(alias="FS_XMLRPC_URL")
    fs_xmlrpc_user: str = Field(alias="FS_XMLRPC_USER", default="freeswitch")
    fs_xmlrpc_password: str = Field(alias="FS_XMLRPC_PASSWORD", default="works")

    # recording transport
    fpbx_recording_storage: str = Field(alias="FPBX_RECORDING_STORAGE", default="local")
    fs_ssh_host: str = Field(alias="FS_SSH_HOST", default="")
    fs_ssh_port: int = Field(alias="FS_SSH_PORT", default=22)
    fs_ssh_user: str = Field(alias="FS_SSH_USER", default="root")
    fs_ssh_key_path: str = Field(alias="FS_SSH_KEY_PATH", default="")
    fs_recordings_dir: str = Field(alias="FS_RECORDINGS_DIR", default="")

    # google tts
    # Path to the Google service-account JSON used for Text-to-Speech.
    google_tts_credentials_file: str = Field(alias="GOOGLE_TTS_CREDENTIALS_FILE", default="")
    google_tts_voice: str = Field(alias="GOOGLE_TTS_VOICE", default="en-US-Neural2-C")
    google_tts_language: str = Field(alias="GOOGLE_TTS_LANGUAGE", default="en-US")

    # extension pool
    ext_pool_start: int = Field(alias="EXT_POOL_START", default=9550)
    ext_pool_end: int = Field(alias="EXT_POOL_END", default=9599)

    @property
    def webauthn_rp_id(self) -> str:
        # the registrable domain: host of APP_BASE_URL without port
        from urllib.parse import urlparse

        return urlparse(self.app_base_url).hostname or "localhost"

    @property
    def webauthn_origin(self) -> str:
        from urllib.parse import urlparse

        u = urlparse(self.app_base_url)
        return f"{u.scheme}://{u.netloc}"

    @property
    def mfa_issuer(self) -> str:
        return self.app_org_name or "FusionPBX IVR Manager"

    @property
    def recordings_dir(self) -> str:
        # allow ${FPBX_DOMAIN_NAME} placeholder in the configured path
        return self.fs_recordings_dir.replace("${FPBX_DOMAIN_NAME}", self.fpbx_domain_name)

    @property
    def session_https_only(self) -> bool:
        """Whether the session cookie is marked Secure. A Secure cookie is not
        sent over plain HTTP, so serving the app over http:// with this on causes
        an endless redirect back to login. Derive from the APP_BASE_URL scheme
        unless explicitly overridden."""
        raw = self.session_https_only_raw.strip().lower()
        if raw:
            return raw in ("1", "true", "yes", "on")
        return self.app_base_url.lower().startswith("https")

    @property
    def base_path(self) -> str:
        """Normalized mount sub-path: "" at root, else "/prefix" (no trailing
        slash). Explicit BASE_PATH wins; otherwise derived from APP_BASE_URL's
        path so setting the full external URL is enough."""
        from urllib.parse import urlparse

        raw = self.base_path_raw.strip() or urlparse(self.app_base_url).path
        raw = raw.strip("/")
        return f"/{raw}" if raw else ""

    @property
    def redirect_uri(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/auth/callback"

    @property
    def entra_authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.entra_tenant_id}"


settings = Settings()  # type: ignore[call-arg]
