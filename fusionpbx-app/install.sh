#!/usr/bin/env bash
#
# Install the IVR Manager PHP app into a FusionPBX installation and register it
# (permissions, menu, default settings). Idempotent — safe to re-run to update.
#
# Usage:  sudo ./fusionpbx-app/install.sh
# Non-interactive: sudo FUSIONPBX_DIR=/var/www/fusionpbx WEB_USER=www-data \
#                       RUN_UPGRADE=yes ASSUME_YES=1 ./fusionpbx-app/install.sh
#
set -euo pipefail

APP=ivr_manager
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/$APP" && pwd)"

die() { echo "error: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "please run as root (sudo $0)"

ask() {
  local prompt="$1" default="$2" reply
  if [ "${ASSUME_YES:-}" = "1" ]; then echo "$default"; return; fi
  read -r -p "$prompt [$default]: " reply </dev/tty || true
  echo "${reply:-$default}"
}
ask_yn() { case "$(ask "$1" "$2")" in [Yy]*) return 0;; *) return 1;; esac }

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

# --- web/service user that owns the FusionPBX tree ---
WEB_USER="${WEB_USER:-$(stat -c '%U' "$FUSIONPBX_DIR/resources/require.php" 2>/dev/null || echo www-data)}"
WEB_GROUP="${WEB_GROUP:-$(stat -c '%G' "$FUSIONPBX_DIR/resources/require.php" 2>/dev/null || echo www-data)}"

DEST="$FUSIONPBX_DIR/app/$APP"
echo "== IVR Manager (FusionPBX app) installer =="
echo "  FusionPBX: $FUSIONPBX_DIR"
echo "  app dir:   $DEST"
echo "  owner:     $WEB_USER:$WEB_GROUP"

# --- copy the app into place ---
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
if [ "${RUN_UPGRADE:-}" = "yes" ] || { [ "${RUN_UPGRADE:-}" != "no" ] && ask_yn "Run FusionPBX app defaults now (register menu/permissions)?" "Y"; }; then
  if [ -f "$UPGRADE" ]; then
    echo "running app defaults as $WEB_USER ..."
    sudo -u "$WEB_USER" php "$UPGRADE" || die "upgrade failed — run it from the GUI: Advanced > Upgrade > App/Permission/Menu Defaults"
    echo "app defaults applied."
  else
    echo "note: $UPGRADE not found — register from the GUI: Advanced > Upgrade > App/Permission/Menu Defaults."
  fi
else
  echo "skipped. Register from the GUI: Advanced > Upgrade > App/Permission/Menu Defaults."
fi

echo
echo "== done =="
echo "Sign out/in, then open 'IVR Manager' from the menu."
echo "Set the extension pool under Advanced > Default Settings > category 'ivr_manager'."
