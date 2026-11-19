# Turquoise Radar on Google Compute Engine

One `e2-small` Debian 12 VM runs the web app and the scheduler as systemd services.
No public ports are opened; access is over Tailscale.

## 1. Create the VM (from your PC, gcloud CLI logged in)

```bash
gcloud config set project 100948930799
gcloud services enable compute.googleapis.com

gcloud compute instances create radar \
  --zone=us-west1-b \
  --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=10GB \
  --tags=radar \
  --shielded-secure-boot
```

No firewall rule is created for port 8000 on purpose. SSH uses Google's default rule.

## 2. Install

```bash
gcloud compute ssh radar --zone=us-west1-b
sudo -i
curl -fsSL https://raw.githubusercontent.com/lucasfarmer20-netizen/Turquoise_Radar/scheduler/deploy/gce/install.sh | bash -s -- scheduler
```

Then fill in `/opt/radar/.env` (`nano /opt/radar/.env`): eBay production keys and flags,
`ANTHROPIC_API_KEY`, `VISION_DAILY_BUDGET_USD`, `RADAR_SCHEDULE_HOURS=1`.

## 3. Bring your data (optional, keeps items, decisions and triage verdicts)

From the PC, with the PC scheduler and uvicorn stopped:

```powershell
gcloud compute scp C:\Users\lucas\radar\data\radar.db radar:/tmp/radar.db --zone=us-west1-b
```

On the VM:

```bash
mv /tmp/radar.db /opt/radar/data/radar.db && chown radar:radar /opt/radar/data/radar.db
```

Skip this and the VM starts empty; the first cycle fills it.

## 4. Start

```bash
cd /opt/radar && sudo -u radar env RADAR_DATABASE_URL=sqlite:////opt/radar/data/radar.db .venv/bin/python -m radar.scheduler seed
systemctl start radar-web radar-scheduler
journalctl -fu radar-scheduler      # first cycle within a few seconds
```

## 5. Reach it: Tailscale

On the VM:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --ssh
```

Open the printed login link once. Install Tailscale on your phone and laptop with the same
account. The VM appears as `radar`; open `http://radar:8000/radar` (MagicDNS) from any of
your devices. With `--ssh` you can also `ssh radar` over Tailscale and stop using gcloud ssh.

Alternative without Tailscale (laptop only):
`gcloud compute start-iap-tunnel radar 8000 --local-host-port=localhost:8000 --zone=us-west1-b`
then open `http://localhost:8000/radar`.

## Operate

```bash
systemctl status radar-web radar-scheduler
journalctl -u radar-scheduler --since "1 hour ago"
sudo /opt/radar/deploy/gce/update.sh             # pull latest, re-seed, restart
cd /opt/radar && sudo -u radar env RADAR_DATABASE_URL=sqlite:////opt/radar/data/radar.db .venv/bin/python -m radar.triage status
```

Backups: `data/radar.db` is the whole state. `gcloud compute scp radar:/opt/radar/data/radar.db .`
copies it back to your PC any time.

Cost: e2-small in us-west1 is roughly $13–15/month plus a few cents of disk. Stop the VM
(`gcloud compute instances stop radar`) to pause billing for compute.
