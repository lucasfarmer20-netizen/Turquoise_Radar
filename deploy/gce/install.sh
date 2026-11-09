#!/usr/bin/env bash
# One-shot install on a fresh Debian 12 Compute Engine VM. Run as root (sudo -i).
#   curl -fsSL https://raw.githubusercontent.com/lucasfarmer20-netizen/Turquoise_Radar/scheduler/deploy/gce/install.sh | bash -s -- scheduler
# Argument: git branch to deploy (default: main).
set -euo pipefail
BRANCH="${1:-main}"
REPO="https://github.com/lucasfarmer20-netizen/Turquoise_Radar.git"
APP=/opt/radar

echo "== packages"
apt-get update -qq
apt-get install -y -qq git curl python3 python3-venv ca-certificates >/dev/null

echo "== uv"
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null
fi

echo "== user + checkout"
id -u radar >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin radar
if [ ! -d "$APP/.git" ]; then
  git clone -q -b "$BRANCH" "$REPO" "$APP"
fi
mkdir -p "$APP/data"
chown -R radar:radar "$APP"

echo "== python env (uv installs 3.12 if the OS lacks it)"
sudo -u radar -H bash -c "cd $APP && UV_CACHE_DIR=$APP/.uv-cache uv sync --frozen --python 3.12" >/dev/null

echo "== .env"
if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  chown radar:radar "$APP/.env"; chmod 600 "$APP/.env"
  echo "   created $APP/.env from the example; EDIT IT before starting services"
fi

echo "== systemd"
cp "$APP/deploy/gce/radar-web.service" "$APP/deploy/gce/radar-scheduler.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable radar-web radar-scheduler >/dev/null

cat <<MSG

Installed to $APP on branch $BRANCH.

Next:
  1. nano $APP/.env            # eBay keys, EBAY_MODE=production, ANTHROPIC_API_KEY, budgets
  2. sudo -u radar $APP/.venv/bin/python -m radar.scheduler seed     (run from $APP)
  3. (optional) copy your PC database to $APP/data/radar.db, chown radar:radar
  4. systemctl start radar-web radar-scheduler
  5. journalctl -fu radar-scheduler       # watch the first cycle

Updates later: $APP/deploy/gce/update.sh
MSG
