# Deployment

The app runs two ways — a **Docker container** or a **standalone systemd
service**. Both are designed to run on (or next to) the FusionPBX host and both
follow least-privilege principles. Pick one.

## Shared prerequisites (do these first, either path)

### 1. A least-privilege database role

Never point the app at the `fusionpbx` owner/superuser role. Create a scoped role
that can touch only the tables the app uses:

```bash
sudo -u postgres psql -d fusionpbx -f sql/least_privilege_role.sql
# edit the password inside the file first, or ALTER ROLE afterwards
```

Grants: `SELECT` on `v_domains`, `v_extensions`; `SELECT/INSERT/UPDATE/DELETE` on
`v_dialplans`; `SELECT/INSERT/UPDATE` on `v_recordings`. Nothing else.

### 2. FreeSWITCH mod_xml_rpc

Enable `mod_xml_rpc` (FusionPBX → Advanced → Settings) and set a strong password.
Firewall the port (`8787`) to localhost/the app only.

### 3. Recording storage — pick the lower-privilege option

| Mode (`FPBX_RECORDING_STORAGE`) | Privilege footprint |
|---|---|
| `db` (**recommended**) | No filesystem access to FreeSWITCH at all — greeting bytes go in `v_recordings`. Simplest and least-privilege. |
| `local` | App must write the FreeSWITCH recordings dir → needs group membership / a bind mount. Use only if your dialplan plays from disk. |

### 4. Secrets

Put config in an env file with `chmod 600`, owned by root (readable by the service
user). Never commit it. Generate `APP_SECRET_KEY` with
`python -c 'import secrets;print(secrets.token_urlsafe(48))'`.

**Google TTS uses a service account** (no API keys). Provide a service-account
JSON with the *Cloud Text-to-Speech* role, `chmod 600`, and point
`GOOGLE_TTS_CREDENTIALS_FILE` at it. In Docker, mount it read-only, e.g.
`-v /etc/fpbx-ivr-manager/google-tts.json:/app/data/google-tts.json:ro` and set
`GOOGLE_TTS_CREDENTIALS_FILE=/app/data/google-tts.json`.

### 5. TLS + hostname

Serve over HTTPS behind nginx (`deploy/nginx-fpbx-ivr-manager.conf`). Set
`APP_BASE_URL=https://<hostname>` — it drives the OAuth redirect URI, secure
cookies, and the WebAuthn/passkey origin. Passkeys **require** a secure context
and a stable hostname.

---

## Option A — Docker

Least-privilege posture baked into `Dockerfile` + `compose.yaml`: non-root user,
read-only root filesystem, all capabilities dropped, `no-new-privileges`, tmpfs
`/tmp`, memory/pids limits, and loopback-only port publishing.

### Build or pull

```bash
# Pull the published multi-arch image (amd64 + arm64):
docker pull ghcr.io/brett6320/fpbx_ivr_manager:latest

# …or build locally. Add optional backends with EXTRAS:
docker build -t fpbx_ivr_manager --build-arg EXTRAS='[ldap,passkey]' .
```

### Configure

```bash
cp .env.example .env && chmod 600 .env
# In .env, because the app runs in a container reaching the host:
#   FPBX_DB_HOST=host.docker.internal
#   FS_XMLRPC_URL=http://host.docker.internal:8787/RPC2
#   FPBX_RECORDING_STORAGE=db          # avoids mounting host paths
```

### Run

```bash
docker compose up -d
docker compose logs -f
curl -sf http://127.0.0.1:8080/healthz     # {"ok":true}
```

`compose.yaml` maps `host.docker.internal` to the host gateway so the container
reaches host Postgres/xmlrpc without host networking.

### If you must use `local` recording storage

1. Find the FreeSWITCH group gid: `getent group freeswitch`.
2. Start with that gid so the container process can write the mount:
   `FREESWITCH_GID=<gid> docker compose up -d --build`.
3. Uncomment the recordings **bind mount** in `compose.yaml` and set
   `FS_RECORDINGS_DIR=/recordings` in `.env`.

The container still runs as uid `10001` (non-root) with only the supplementary
group needed to write that one directory — nothing more.

### Least-privilege checklist (Docker)

- [x] Non-root uid, `nologin` shell, no home
- [x] `read_only: true` root fs; writable state confined to the `ivr_data` volume
- [x] `cap_drop: [ALL]`, `no-new-privileges`
- [x] Port published only on `127.0.0.1`
- [x] Scoped DB role (not `fusionpbx`)
- [x] `db` recording storage → no host filesystem mount
- [x] `mem_limit` / `pids_limit` set

---

## Option B — Standalone (systemd)

For hosts without Docker. Uses a dedicated system user and a hardened unit
(`deploy/fpbx-ivr-manager.service`).

### Quick install (script)

The interactive installer does everything below — prompts for the install
location (default `/opt/ivr-manager`), creates the service user + venv + env
file + systemd unit, and asks whether to start at boot:

```bash
git clone https://github.com/brett6320/fpbx_ivr_manager
cd fpbx_ivr_manager
sudo ./deploy/install.sh
# then edit the generated /etc/fpbx-ivr-manager/env and start the service
```

It auto-detects the `freeswitch` group (for local recording storage) and falls
back to a private group + db storage when it's absent. Non-interactive:
`sudo INSTALL_DIR=/srv/ivr START_AT_BOOT=no EXTRAS=fpbx ASSUME_YES=1 ./deploy/install.sh`.

**Python 3.11+ is required.** The installer picks the newest suitable interpreter
(`python3.14`…`python3.11`) and errors clearly if none is found. If your distro's
default `python3` is older than 3.11 (common on the FusionPBX Debian base), install
a newer one and either let the installer auto-detect it or point it explicitly:

```bash
sudo apt install python3.11 python3.11-venv       # Debian/Ubuntu
sudo PYTHON=/usr/bin/python3.11 ./deploy/install.sh
```

The manual steps below are equivalent, if you'd rather do it by hand.

### Install (manual)

```bash
sudo mkdir -p /opt/ivr-manager
sudo git clone https://github.com/brett6320/fpbx_ivr_manager /opt/ivr-manager
cd /opt/ivr-manager

# Dedicated, unprivileged service account (declarative via systemd-sysusers).
# The sample adds ivrmgr to 'freeswitch' — remove that line if you use db storage.
sudo cp deploy/fpbx-ivr-manager.sysusers.conf /etc/sysusers.d/fpbx-ivr-manager.conf
sudo systemd-sysusers
#   (equivalent manual form:)
#   sudo useradd --system --no-create-home --shell /usr/sbin/nologin --groups freeswitch ivrmgr

# Isolated virtualenv (add [ldap]/[passkey] extras as needed).
sudo python3 -m venv .venv
sudo .venv/bin/pip install '.[ldap,passkey]'   # or just '.' for local/TOTP only
sudo chown -R root:root /opt/ivr-manager   # app code owned by root, run read-only
```

### Configure

```bash
sudo install -d -m 750 -o root -g ivrmgr /etc/fpbx-ivr-manager
# Native-oriented sample (localhost DB/xmlrpc, StateDirectory path, db storage):
sudo cp deploy/fpbx-ivr-manager.env.sample /etc/fpbx-ivr-manager/env
sudo chown root:ivrmgr /etc/fpbx-ivr-manager/env
sudo chmod 640 /etc/fpbx-ivr-manager/env
sudo "${EDITOR:-vi}" /etc/fpbx-ivr-manager/env      # fill REPLACE_ME values
```

### Enable

```bash
sudo cp deploy/fpbx-ivr-manager.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fpbx-ivr-manager
systemctl status fpbx-ivr-manager
curl -sf http://127.0.0.1:8080/healthz
```

Manage local users/admins with the CLI (runs as the service user):

```bash
sudo -u ivrmgr .venv/bin/python manage.py add alice --admin
sudo -u ivrmgr .venv/bin/python manage.py group-add alice ivr-editors
```

### Least-privilege checklist (systemd)

The unit enforces: `NoNewPrivileges`, `ProtectSystem=strict` (whole fs read-only
except the state dir and, if needed, the recordings path), `ProtectHome`,
`PrivateTmp`, `PrivateDevices`, empty `CapabilityBoundingSet`,
`MemoryDenyWriteExecute`, a `@system-service` syscall filter, and `UMask=0077`.
The app binds `127.0.0.1` only.

- [x] Dedicated unprivileged `ivrmgr` user; code owned by root, run read-only
- [x] Env file `0640 root:ivrmgr`
- [x] State dir `0700` via `StateDirectory`
- [x] `ReadWritePaths` limited to the recordings dir (drop it entirely with `db` storage)
- [x] Scoped DB role
- [x] nginx TLS in front; app on loopback

---

## Reverse proxy (both paths)

```bash
sudo cp deploy/nginx-fpbx-ivr-manager.conf /etc/nginx/sites-available/ivr.conf
sudo ln -s /etc/nginx/sites-available/ivr.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Set `APP_BASE_URL=https://<server_name>` to match. For Entra SSO, register the
redirect URI `https://<server_name>/auth/callback` (see
[entra-sso.md](entra-sso.md)).

## Upgrades

- **Docker**: `docker compose pull && docker compose up -d` (or rebuild).
- **systemd**: `git pull && sudo .venv/bin/pip install -e . && sudo systemctl restart fpbx-ivr-manager`.

Published images are versioned per release (see the Releases page and
`ghcr.io/brett6320/fpbx_ivr_manager` tags). Pin a version tag in production
rather than `latest`.
