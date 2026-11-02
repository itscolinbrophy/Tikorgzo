#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${TIKORGZO_ENV_FILE:-/etc/tikorgzo-web.env}"
SERVICE_NAME="tikorgzo-web"

[[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE; run the installer first." >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

INSTALL_DIR="${TIKORGZO_INSTALL_DIR:-/opt/tikorgzo-web}"
BRANCH="${TIKORGZO_BRANCH:-main}"

echo "Updating $INSTALL_DIR ($BRANCH) ..."
git -C "$INSTALL_DIR" fetch --all --prune
git -C "$INSTALL_DIR" checkout "$BRANCH"
git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"

"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade -e "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade -r "$INSTALL_DIR/web/requirements.txt"

systemctl restart "$SERVICE_NAME"
echo "Updated and restarted $SERVICE_NAME."
