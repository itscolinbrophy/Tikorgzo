#!/usr/bin/env bash
set -euo pipefail
umask 022

REPO_URL="${TIKORGZO_REPO_URL:-https://github.com/itscolinbrophy/Tikorgzo.git}"
BRANCH="${TIKORGZO_BRANCH:-main}"
INSTALL_DIR="${TIKORGZO_INSTALL_DIR:-/opt/tikorgzo-web}"
DATA_ROOT="${TIKORGZO_DATA_ROOT:-/var/lib/tikorgzo-web}"
DOWNLOAD_DIR="${TIKORGZO_DOWNLOAD_DIR:-$DATA_ROOT/downloads}"
PORT="${TIKORGZO_PORT:-8080}"
HOST="${TIKORGZO_HOST:-0.0.0.0}"
SERVICE_USER="${TIKORGZO_USER:-tikorgzo}"
SERVICE_NAME="tikorgzo-web"
ENV_FILE="/etc/${SERVICE_NAME}.env"

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || die "run this as root (sudo bash ...)"
command -v apt-get >/dev/null 2>&1 || die "this installer supports Debian/Ubuntu only"
command -v systemctl >/dev/null 2>&1 || die "systemd not found; use the Docker setup instead"

export DEBIAN_FRONTEND=noninteractive

log "Installing system dependencies"
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl git python3 python3-venv python3-pip \
  ffmpeg xvfb xauth wget gnupg fonts-liberation >/dev/null

if ! command -v google-chrome >/dev/null 2>&1 && ! command -v google-chrome-stable >/dev/null 2>&1; then
  log "Installing Google Chrome"
  arch="$(dpkg --print-architecture)"
  case "$arch" in
    amd64) chrome_arch="amd64" ;;
    arm64) chrome_arch="arm64" ;;
    *) die "unsupported architecture: $arch" ;;
  esac
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT
  wget -q -O "$tmp_dir/chrome.deb" "https://dl.google.com/linux/direct/google-chrome-stable_current_${chrome_arch}.deb"
  apt-get install -y -qq "$tmp_dir/chrome.deb" >/dev/null
fi

log "Creating service user and data directories"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$DATA_ROOT" --shell /usr/sbin/nologin "$SERVICE_USER"
fi
install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$DATA_ROOT" "$DOWNLOAD_DIR" "$DATA_ROOT/.thumbnails" "$DATA_ROOT/.jobs"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_ROOT"

log "Fetching $REPO_URL"
if [[ -d "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
  die "$INSTALL_DIR exists but is not a git checkout"
fi
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --all --prune
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only "origin" "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

log "Installing Python environment"
python_bin=""
for candidate in python3.13 python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
    && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
    python_bin="$(command -v "$candidate")"
    break
  fi
done

if [[ -n "$python_bin" ]]; then
  "$python_bin" -m venv "$INSTALL_DIR/.venv"
else
  log "Python 3.12+ not found; installing a uv-managed Python 3.12"
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  command -v uv >/dev/null 2>&1 || die "failed to install uv"
  if [[ "$(command -v uv)" != "/usr/local/bin/uv" ]]; then
    install -m 0755 "$(command -v uv)" /usr/local/bin/uv
  fi
  export UV_PYTHON_INSTALL_DIR=/usr/local/share/uv/python
  uv python install 3.12
  uv venv --seed --python 3.12 "$INSTALL_DIR/.venv"
fi

"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip wheel
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade -e "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade -r "$INSTALL_DIR/web/requirements.txt"

log "Writing configuration"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
TIKORGZO_DATA_DIR="$DOWNLOAD_DIR"
TIKORGZO_PORT="$PORT"
TIKORGZO_HOST="$HOST"
TIKORGZO_INSTALL_DIR="$INSTALL_DIR"
TIKORGZO_REPO_URL="$REPO_URL"
TIKORGZO_BRANCH="$BRANCH"
TIKORGZO_USE_XVFB=1
TIKORGZO_MAX_CONCURRENT=1
TIKORGZO_CHROME_ARGS="--no-sandbox --disable-dev-shm-usage --disable-gpu"
EOF
  chmod 0644 "$ENV_FILE"
else
  log "Keeping existing $ENV_FILE"
fi

log "Installing launcher and update command"
cat > "/usr/local/bin/${SERVICE_NAME}-run" <<EOF
#!/usr/bin/env bash
set -euo pipefail
set -a
source "$ENV_FILE"
set +a
export HOME="$DATA_ROOT"
export PATH="$INSTALL_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin"
export TIKORGZO_BIN="$INSTALL_DIR/.venv/bin/tikorgzo"
exec "$INSTALL_DIR/.venv/bin/uvicorn" web.app:app \\
  --app-dir "$INSTALL_DIR" \\
  --host "\${TIKORGZO_HOST:-0.0.0.0}" \\
  --port "\${TIKORGZO_PORT:-8080}"
EOF
chmod 0755 "/usr/local/bin/${SERVICE_NAME}-run"

install -m 0755 "$INSTALL_DIR/deploy/update.sh" /usr/local/bin/tikorgzo-update

log "Installing systemd service"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Tikorgzo Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
Environment=HOME=$DATA_ROOT
ExecStart=/usr/local/bin/${SERVICE_NAME}-run
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

for _ in $(seq 1 15); do
  systemctl is-active --quiet "$SERVICE_NAME" && break
  sleep 1
done

if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo
  journalctl -u "$SERVICE_NAME" -n 60 --no-pager || true
  die "service failed to start"
fi

ip_address="$(hostname -I 2>/dev/null | awk '{print $1}')"
ip_address="${ip_address:-<container-ip>}"

cat <<EOF

$(printf '\033[1;32mTikorgzo Web is running.\033[0m')

  URL:      http://$ip_address:$PORT
  Data:     $DOWNLOAD_DIR
  Logs:     journalctl -u $SERVICE_NAME -f
  Update:   sudo tikorgzo-update

EOF
