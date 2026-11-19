#!/usr/bin/env bash
# Pull the deployed branch, sync deps, re-seed queries, restart both services.
set -euo pipefail
APP=/opt/radar
cd "$APP"
sudo -u radar -H git pull -q --ff-only
sudo -u radar -H bash -c "UV_CACHE_DIR=$APP/.uv-cache uv sync --frozen"
# A 2 GB VM with no swap freezes outright under memory pressure; give it 2 GB of swap once.
if ! swapon --show | grep -q /swapfile; then
  [ -f /swapfile ] || { fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null; }
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
# Service definitions live in the repo; reinstall them so limits and options take effect.
install -m 644 "$APP"/deploy/gce/radar-web.service "$APP"/deploy/gce/radar-scheduler.service /etc/systemd/system/
systemctl daemon-reload
systemctl stop radar-scheduler
sudo -u radar -H env RADAR_DATABASE_URL=sqlite:////opt/radar/data/radar.db "$APP/.venv/bin/python" -m radar.scheduler seed
systemctl restart radar-web
systemctl start radar-scheduler
systemctl --no-pager status radar-web radar-scheduler | grep -E "●|Active"
