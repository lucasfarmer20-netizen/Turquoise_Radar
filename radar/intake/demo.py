"""Explicit synthetic demo only; defaults to a separate alerts-demo database."""

import argparse
import json
from pathlib import Path

from radar.anomalies import seed_patterns
from radar.db import database
from radar.discovery.service import seed_discovery
from radar.intake.registry import seed_sources
from radar.intake.service import import_alerts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="sqlite:///./data/alerts-demo.db")
    args = parser.parse_args()
    engine, sessions = database(args.database)
    root = Path(__file__).resolve().parents[2] / "fixtures/alerts"
    with sessions.begin() as s:
        seed_patterns(s)
        seed_discovery(s)
        seed_sources(s)
        for entry in json.loads((root / "manifest.json").read_text()):
            batch = import_alerts(
                s, entry["source"], [(root / f).read_bytes() for f in entry["files"]]
            )
            print(
                "SYNTHETIC fixture intake:",
                entry["source"],
                {k: v for k, v in batch.stats.items() if k != "message_ids"},
            )
    engine.dispose()


if __name__ == "__main__":
    main()
