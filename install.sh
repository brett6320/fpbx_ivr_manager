#!/usr/bin/env bash
#
# Top-level installer: detects what this host can run, RECOMMENDS a variant, and
# verifies compatibility BEFORE taking any action — then dispatches to the right
# sub-installer.
#
#   Python service (Docker/systemd)  -> deploy/install.sh          (needs Python 3.11+)
#   FusionPBX PHP app                -> fusionpbx-app/install.sh   (runs under FusionPBX's PHP)
#
# Usage:  sudo ./install.sh          (interactive)
#         ./install.sh --check       (just print the compatibility report)
# Non-interactive: sudo VARIANT=python|php ASSUME_YES=1 ./install.sh
#
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIN_PY_MAJOR=3; MIN_PY_MINOR=11

say() { printf '%s\n' "$*"; }
ok()  { printf '  [ok]   %s\n' "$*"; }
no()  { printf '  [--]   %s\n' "$*"; }

# ---- probe the environment (no root or side effects) ----
PY_BIN=""; PY_VER=""
for c in "${PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
  [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 || continue
  if "$c" -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=($MIN_PY_MAJOR,$MIN_PY_MINOR) else 1)" 2>/dev/null; then
    PY_BIN="$(command -v "$c")"; PY_VER="$("$c" -V 2>&1)"; break
  fi
done

DOCKER_OK=no
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then DOCKER_OK=yes; fi

FPBX_DIR=""
for d in "${FUSIONPBX_DIR:-}" /var/www/fusionpbx /usr/share/fusionpbx /srv/www/fusionpbx; do
  [ -n "$d" ] && [ -f "$d/resources/require.php" ] && { FPBX_DIR="$d"; break; }
done

PHP_VER=""
command -v php >/dev/null 2>&1 && PHP_VER="$(php -r 'echo PHP_VERSION;' 2>/dev/null || true)"

# python variant is viable if a suitable interpreter exists OR docker can run it
PY_VIABLE=no; { [ -n "$PY_BIN" ] || [ "$DOCKER_OK" = yes ]; } && PY_VIABLE=yes
# php variant is viable if a FusionPBX install and a php cli are present
PHP_VIABLE=no; { [ -n "$FPBX_DIR" ] && [ -n "$PHP_VER" ]; } && PHP_VIABLE=yes

# ---- recommend ----
if [ "$PY_VIABLE" = yes ]; then
  RECOMMEND=python
  REASON="a suitable runtime is available and the Python app is the full-featured variant"
elif [ "$PHP_VIABLE" = yes ]; then
  RECOMMEND=php
  REASON="no Python 3.11+/Docker here, but FusionPBX is present — the PHP app runs natively"
else
  RECOMMEND=none
  REASON="neither runtime is ready on this host"
fi

# ---- report ----
say "== FusionPBX IVR Manager — compatibility check =="
say "Python service variant (deploy/install.sh):"
[ -n "$PY_BIN" ] && ok "Python $PY_VER at $PY_BIN" || no "no Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} found"
[ "$DOCKER_OK" = yes ] && ok "Docker available (bundles its own Python)" || no "Docker not available"
[ "$PY_VIABLE" = yes ] && ok "=> viable" || no "=> NOT viable (need Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ or Docker)"
say ""
say "FusionPBX PHP app variant (fusionpbx-app/install.sh):"
[ -n "$FPBX_DIR" ] && ok "FusionPBX at $FPBX_DIR" || no "FusionPBX not found"
[ -n "$PHP_VER" ] && ok "PHP $PHP_VER" || no "php cli not found"
[ "$PHP_VIABLE" = yes ] && ok "=> viable" || no "=> NOT viable (need a FusionPBX install + php)"
say ""
if [ "$RECOMMEND" = none ]; then
  say "Recommendation: none — $REASON."
  say "  Python variant: install Python 3.11+ (or Docker); see docs/deployment.md."
  say "  PHP variant: run this on the FusionPBX host."
  exit 1
fi
say "Recommendation: ${RECOMMEND}  ($REASON)."

# --check just prints the report
if [ "${1:-}" = "--check" ]; then exit 0; fi

# ---- choose ----
CHOICE="${VARIANT:-}"
if [ -z "$CHOICE" ]; then
  if [ "${ASSUME_YES:-}" = "1" ]; then
    CHOICE="$RECOMMEND"
  else
    printf 'Install which variant? [1] Python service  [2] FusionPBX PHP app  (default: %s) : ' "$RECOMMEND"
    read -r ans </dev/tty || true
    case "$ans" in
      1|python|py) CHOICE=python ;;
      2|php|fusionpbx) CHOICE=php ;;
      "") CHOICE="$RECOMMEND" ;;
      *) say "unrecognized choice"; exit 1 ;;
    esac
  fi
fi

# ---- gate: verify compatibility for the CHOSEN variant before acting ----
if [ "$CHOICE" = python ] && [ "$PY_VIABLE" != yes ]; then
  say "Refusing: the Python variant needs Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ or Docker, which isn't available here."
  say "Install one, or choose the PHP app (VARIANT=php)."
  exit 1
fi
if [ "$CHOICE" = php ] && [ "$PHP_VIABLE" != yes ]; then
  say "Refusing: the PHP app needs a FusionPBX install + php on this host (not found)."
  say "Run it on the FusionPBX host, or choose the Python variant (VARIANT=python)."
  exit 1
fi

# ---- dispatch (sub-installers require root) ----
if [ "$(id -u)" -ne 0 ]; then
  say "Compatibility OK for '$CHOICE'. Re-run with sudo to install:  sudo VARIANT=$CHOICE ./install.sh"
  exit 0
fi
if [ "$CHOICE" = python ]; then
  say "-> launching deploy/install.sh"
  exec "$REPO/deploy/install.sh"
else
  say "-> launching fusionpbx-app/install.sh"
  exec "$REPO/fusionpbx-app/install.sh"
fi
