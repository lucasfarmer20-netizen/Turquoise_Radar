#!/usr/bin/env bash
# Pull the deployed branch, sync deps, re-seed queries, restart both services.
set -euo pipefail
APP=/opt/radar
cd "$APP"
sudo -u radar -H git pull -q --ff-only
sudo -u radar -H bash -c "UV_CACHE_DIR=$APP/.uv-cache uv sync --frozen"
systemctl stop radar-scheduler
sudo -u radar -H env RADAR_DATABASE_URL=sqlite:////opt/radar/data/radar.db "$APP/.venv/bin/python" -m radar.scheduler seed
systemctl restart radar-web
systemctl start radar-scheduler
systemctl --no-pager status radar-web radar-scheduler | grep -E "●|Active"
