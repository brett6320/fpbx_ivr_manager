#!/usr/bin/env bash
#
# Install / re-install / verify the IVR Manager PHP app in FusionPBX.
#
# Safe to run any number of times: if an install already exists it is VERIFIED
# and you are asked to confirm BEFORE anything is overwritten. It then re-copies
# the app, re-applies defaults, and validates the install (files, php syntax, and
# — via FusionPBX's own DB config — that the permissions and menu item exist).
#
# Usage:
#   sudo ./fusionpbx-app/install.sh            # install/update, then verify
#   sudo ./fusionpbx-app/install.sh --verify   # validate only, change nothing
#
# Non-interactive: sudo FUSIONPBX_DIR=/var/www/fusionpbx RUN_UPGRADE=yes \
#                       ASSUME_YES=1 ./fusionpbx-app/install.sh
#
set -euo pipefail

APP=ivr_manager
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/$APP" && pwd)"
VERIFY_ONLY=no
[ "${1:-}" = "--verify" ] && VERIFY_ONLY=yes

die() { echo "error: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "please run as root (sudo $0)"

ask() {
  local prompt="$1" default="$2" reply
  if [ "${ASSUME_YES:-}" = "1" ]; then echo "$default"; return; fi
  read -r -p "$prompt [$default]: " reply </dev/tty || true
  echo "${reply:-$default}"
}
ask_yn() { case "$(ask "$1" "$2")" in [Yy]*) return 0;; *) return 1;; esac }
is_pos() { [[ "${1:-}" =~ ^[0-9]+$ ]] && [ "$1" -gt 0 ]; }

# --- locate FusionPBX (dir containing resources/require.php) ---
detect_fpbx() {
  local d
  for d in /var/www/fusionpbx /usr/share/fusionpbx /srv/www/fusionpbx; do
    [ -f "$d/resources/require.php" ] && { echo "$d"; return 0; }
  done
  return 1
}
FUSIONPBX_DIR="${FUSIONPBX_DIR:-$(detect_fpbx || true)}"
[ -n "$FUSIONPBX_DIR" ] || FUSIONPBX_DIR="$(ask "FusionPBX directory" "/var/www/fusionpbx")"
[ -f "$FUSIONPBX_DIR/resources/require.php" ] || die "no FusionPBX found at $FUSIONPBX_DIR (resources/require.php missing)"

WEB_USER="${WEB_USER:-$(stat -c '%U' "$FUSIONPBX_DIR/resources/require.php" 2>/dev/null || echo www-data)}"
WEB_GROUP="${WEB_GROUP:-$(stat -c '%G' "$FUSIONPBX_DIR/resources/require.php" 2>/dev/null || echo www-data)}"
DEST="$FUSIONPBX_DIR/app/$APP"

# --- FusionPBX database creds, for registration checks (best-effort) ---
CONF="${FUSIONPBX_CONF:-/etc/fusionpbx/config.conf}"
pgval() { sed -n "s/^database\.0\.$1[[:space:]]*=[[:space:]]*//p" "$CONF" 2>/dev/null | head -1 | tr -d '[:space:]'; }
PG_HOST=""; PG_PORT=""; PG_NAME=""; PG_USER=""; PG_PASS=""
if [ -f "$CONF" ]; then
  PG_HOST="$(pgval host)"; PG_PORT="$(pgval port)"; PG_NAME="$(pgval name)"
  PG_USER="$(pgval username)"; PG_PASS="$(pgval password)"
fi
db_query() {  # echoes the scalar result, or nothing on any failure
  { [ -n "$PG_HOST" ] && command -v psql >/dev/null; } || return 1
  PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "${PG_PORT:-5432}" -U "${PG_USER:-fusionpbx}" \
    -d "${PG_NAME:-fusionpbx}" -tAc "$1" 2>/dev/null | tr -d '[:space:]'
}

verify() {
  local problems=0
  echo "== verifying =="
  echo "  app dir: $DEST"

  # 1. all source files present in the install
  local missing=0 rel
  while IFS= read -r rel; do
    [ -f "$DEST/$rel" ] || { echo "  [--] missing file: $rel"; missing=$((missing+1)); }
  done < <(cd "$SRC_DIR" && find . -type f | sed 's#^\./##')
  if [ "$missing" -eq 0 ]; then echo "  [ok] all app files present"; else problems=$((problems+1)); fi

  # 2. php syntax of the installed files
  if command -v php >/dev/null; then
    if find "$DEST" -name '*.php' -print0 | xargs -0 -n1 php -l >/dev/null 2>&1; then
      echo "  [ok] php syntax valid"
    else
      echo "  [--] php syntax errors in installed files"; problems=$((problems+1))
    fi
  else
    echo "  [~~] php cli not found — skipped syntax check"
  fi

  # 3. ownership
  echo "  [ok] owner: $(stat -c '%U:%G' "$DEST" 2>/dev/null || echo '?')"

  # 4. registration (permissions + menu) via the DB
  if [ -n "$PG_HOST" ] && command -v psql >/dev/null; then
    local p m
    p="$(db_query "select count(*) from v_permissions where permission_name = 'ivr_manager_schedule_view'" || true)"
    m="$(db_query "select count(*) from v_menu_items where menu_item_link like '%/app/${APP}/%'" || true)"
    if is_pos "$p"; then echo "  [ok] permissions registered"; else
      echo "  [--] permissions NOT registered — Advanced > Upgrade > App/Permission Defaults"; problems=$((problems+1)); fi
    if is_pos "$m"; then echo "  [ok] menu item present"; else
      echo "  [!!] menu NOT built — Advanced > Menu Manager > Restore Default Menu, then sign out/in"; fi
  else
    echo "  [~~] no psql/config.conf — verify permissions & menu in the GUI"
  fi

  echo "  direct URL: /app/${APP}/schedules.php"
  [ "$problems" -eq 0 ] && echo "  => OK" || echo "  => $problems issue(s) to address"
  return "$problems"
}

# ---- verify-only: change nothing ----
if [ "$VERIFY_ONLY" = yes ]; then
  echo "== IVR Manager — verify (no changes) =="
  echo "  FusionPBX: $FUSIONPBX_DIR"
  [ -d "$DEST" ] || die "not installed: $DEST is missing (run without --verify to install)"
  verify || exit $?
  exit 0
fi

echo "== IVR Manager (FusionPBX app) installer =="
echo "  FusionPBX: $FUSIONPBX_DIR"
echo "  app dir:   $DEST"
echo "  owner:     $WEB_USER:$WEB_GROUP"

# --- verify any existing install BEFORE overwriting it ---
if [ -d "$DEST" ]; then
  echo
  echo "An existing install was found — checking it before overwriting:"
  verify || true
  echo
  if ! ask_yn "Overwrite the existing install at $DEST (rsync --delete)?" "Y"; then
    echo "Left the existing install unchanged. Re-run with --verify to re-check."
    exit 0
  fi
fi

# --- copy the app into place (idempotent) ---
mkdir -p "$FUSIONPBX_DIR/app"
if command -v rsync >/dev/null; then
  rsync -a --delete "$SRC_DIR"/ "$DEST"/
else
  rm -rf "$DEST"; cp -a "$SRC_DIR" "$DEST"
fi
chown -R "$WEB_USER":"$WEB_GROUP" "$DEST"
echo "copied app to $DEST"

# --- register app defaults (permissions, menu, default settings) ---
UPGRADE="$FUSIONPBX_DIR/core/upgrade/upgrade.php"
if [ "${RUN_UPGRADE:-}" = "yes" ] || { [ "${RUN_UPGRADE:-}" != "no" ] && ask_yn "Run FusionPBX app defaults now (register permissions)?" "Y"; }; then
  if [ -f "$UPGRADE" ]; then
    echo "running app defaults as $WEB_USER ..."
    sudo -u "$WEB_USER" php "$UPGRADE" || echo "note: upgrade returned an error — you can run it from Advanced > Upgrade instead"
  else
    echo "note: $UPGRADE not found — register from Advanced > Upgrade > App/Permission Defaults."
  fi
fi

echo
verify || true

echo
echo "== next =="
echo "MENU: App Defaults does NOT build the menu — go to Advanced > Menu Manager,"
echo "  open your menu (usually 'default'), click 'Restore Default Menu', sign out/in."
echo "Re-run this script any time (or with --verify) to re-check the install."
