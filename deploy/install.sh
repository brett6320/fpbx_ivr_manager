#!/usr/bin/env bash
#
# Interactive installer for the FusionPBX IVR Manager as a hardened systemd
# service. Installs the app to a location you choose (default /opt/ivr-manager),
# creates an unprivileged service user, a Python venv, an env file, and the
# systemd unit — then optionally enables it to start at boot.
#
# Usage:  sudo ./deploy/install.sh
# Non-interactive overrides (env vars): INSTALL_DIR, START_AT_BOOT=yes|no,
#   EXTRAS (e.g. "ldap,fpbx"), ASSUME_YES=1
#
set -euo pipefail

SERVICE=fpbx-ivr-manager
UNIT=/etc/systemd/system/${SERVICE}.service
ENV_DIR=/etc/${SERVICE}
ENV_FILE=${ENV_DIR}/env
SVC_USER=ivrmgr

# repo root = parent of this script's directory
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MIN_PY_MAJOR=3
MIN_PY_MINOR=11   # app requires-python = >=3.11

die() { echo "error: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "please run as root (sudo $0)"
command -v systemctl >/dev/null || die "systemd (systemctl) not found"

# Pick a Python interpreter that satisfies the app's minimum. Honors $PYTHON,
# then tries versioned names newest-first, then a bare python3.
pick_python() {
  local c
  for c in "${PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=($MIN_PY_MAJOR,$MIN_PY_MINOR) else 1)" 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}
PY="$(pick_python)" || die "Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} is required but none was found.
  Install it and re-run, e.g.:  sudo apt install python3.11 python3.11-venv
  or point the installer at a specific interpreter:  sudo PYTHON=/usr/bin/python3.11 $0"

ask() {  # ask "prompt" "default" -> echoes answer
  local prompt="$1" default="$2" reply
  if [ "${ASSUME_YES:-}" = "1" ]; then echo "$default"; return; fi
  read -r -p "$prompt [$default]: " reply </dev/tty || true
  echo "${reply:-$default}"
}
ask_yn() {  # ask_yn "prompt" "Y|N" -> returns 0 for yes
  local ans; ans="$(ask "$1" "$2")"
  case "$ans" in [Yy]*) return 0;; *) return 1;; esac
}

echo "== FusionPBX IVR Manager installer =="
echo "using $("$PY" -V 2>&1) ($PY)"

INSTALL_DIR="${INSTALL_DIR:-$(ask "Install location" "/opt/ivr-manager")}"
[ "${INSTALL_DIR#/}" != "$INSTALL_DIR" ] || die "install location must be an absolute path"
EXTRAS="${EXTRAS:-$(ask "Optional Python extras (comma-sep: ldap,passkey,fpbx) or none" "none")}"
EXTRAS="$(printf '%s' "$EXTRAS" | tr -d '[:space:]')"   # pip rejects spaces in extras
if [ "${START_AT_BOOT:-}" = "" ]; then
  if ask_yn "Start the service automatically at boot?" "Y"; then START_AT_BOOT=yes; else START_AT_BOOT=no; fi
fi

# ---- service account (unprivileged) ----
if ! getent passwd "$SVC_USER" >/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
  echo "created service user $SVC_USER"
fi
# Recordings written to the local FS need membership in 'freeswitch'. If that
# group is absent (db recording storage / non-co-located), fall back to the
# user's own group and drop the freeswitch storage write path from the unit.
if getent group freeswitch >/dev/null; then
  usermod -aG freeswitch "$SVC_USER"
  SVC_GROUP=freeswitch
else
  SVC_GROUP="$SVC_USER"
  echo "note: 'freeswitch' group not found — using group '$SVC_GROUP' and db recording storage"
fi

# ---- copy the app into place (code owned by root, run read-only) ----
mkdir -p "$INSTALL_DIR"
if [ "$SRC_DIR" != "$INSTALL_DIR" ]; then
  if command -v rsync >/dev/null; then
    rsync -a --delete --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
      "$SRC_DIR"/ "$INSTALL_DIR"/
  else
    cp -a "$SRC_DIR"/. "$INSTALL_DIR"/ ; rm -rf "$INSTALL_DIR/.git"
  fi
fi

# ---- python venv + install ----
VPY="$INSTALL_DIR/.venv/bin/python"
# recreate the venv if it's missing or was built with an too-old interpreter
if [ -x "$VPY" ] && ! "$VPY" -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=($MIN_PY_MAJOR,$MIN_PY_MINOR) else 1)" 2>/dev/null; then
  echo "replacing existing venv (built with an older Python)"
  rm -rf "$INSTALL_DIR/.venv"
fi
"$PY" -m venv "$INSTALL_DIR/.venv" 2>/dev/null || die \
  "could not create the virtualenv with $PY — install the venv module, e.g.: sudo apt install ${PY##*/}-venv"
"$VPY" -m pip install --quiet --upgrade pip
if [ "$EXTRAS" = "none" ] || [ -z "$EXTRAS" ]; then
  "$VPY" -m pip install --quiet "$INSTALL_DIR"
else
  "$VPY" -m pip install --quiet "$INSTALL_DIR[$EXTRAS]"
fi
chown -R root:root "$INSTALL_DIR"   # app code root-owned; service runs read-only

# ---- env file (created once; edit before starting) ----
install -d -m 750 -o root -g "$SVC_GROUP" "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
  install -m 640 -o root -g "$SVC_GROUP" "$SRC_DIR/deploy/fpbx-ivr-manager.env.sample" "$ENV_FILE"
  # a fresh random secret so the sample placeholder is never shipped as-is
  secret="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=${secret}|" "$ENV_FILE"
  ENV_CREATED=1
fi

# ---- systemd unit, templated to the chosen paths ----
tmp_unit="$(mktemp)"
sed -e "s|/opt/ivr-manager|$INSTALL_DIR|g" \
    -e "s|^Group=freeswitch|Group=$SVC_GROUP|" \
    "$SRC_DIR/deploy/${SERVICE}.service" > "$tmp_unit"
if [ "$SVC_GROUP" != freeswitch ]; then
  sed -i '\|ReadWritePaths=/var/lib/freeswitch/storage|d' "$tmp_unit"
fi
install -m 644 "$tmp_unit" "$UNIT"
rm -f "$tmp_unit"
systemctl daemon-reload

# ---- enable/start per the boot choice ----
if [ "$START_AT_BOOT" = yes ]; then
  systemctl enable "$SERVICE"
  BOOT_MSG="enabled at boot"
else
  systemctl disable "$SERVICE" >/dev/null 2>&1 || true
  BOOT_MSG="NOT enabled at boot (start manually: systemctl start $SERVICE)"
fi

echo
echo "== installed =="
echo "  app:     $INSTALL_DIR"
echo "  unit:    $UNIT  (User=$SVC_USER Group=$SVC_GROUP)"
echo "  env:     $ENV_FILE"
echo "  boot:    $BOOT_MSG"
if [ "${ENV_CREATED:-}" = 1 ]; then
  echo
  echo "NEXT: edit $ENV_FILE (DB/XMLRPC/domain, APP_BASE_URL, auth), then:"
  echo "  sudo systemctl start $SERVICE && systemctl status $SERVICE"
  echo "A random APP_SECRET_KEY was generated for you."
else
  echo
  echo "Existing env kept. Apply the update with: sudo systemctl restart $SERVICE"
  if [ "$START_AT_BOOT" = yes ]; then systemctl start "$SERVICE" || true; fi
fi
