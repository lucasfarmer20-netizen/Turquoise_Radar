"""Local-only administration: python -m radar.cli init|seed|import FILE."""

import argparse
from pathlib import Path

from sqlalchemy import select

from radar.anomalies import seed_patterns
from radar.db import Listing, Setting, database
from radar.schemas import Preferences
from radar.sources import import_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "seed", "import"])
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()
    engine, sessions = database()
    with sessions.begin() as s:
        seed_patterns(s)
        if args.command == "seed":
            fixtures = Path(__file__).resolve().parent.parent / "fixtures"
            if not s.get(Setting, 1):
                # Explicitly synthetic spot and fee assumptions, never current market data.
                prefs = Preferences.model_validate_json(
                    (fixtures / "demo_settings.json").read_text()
                )
                s.add(Setting(id=1, data=prefs.model_dump(mode="json")))
            result = import_rows(s, (fixtures / "demo.json").read_text(), "json")
            print("SYNTHETIC demo import:", result)
        elif args.command == "import":
            if not args.file:
                parser.error("import requires a CSV or JSON file")
            path = Path(args.file)
            print(import_rows(s, path.read_text(encoding="utf-8-sig"), path.suffix.lstrip(".")))
        else:
            print("Database initialized. No demo data added.")
        print("Tracked listings:", len(list(s.scalars(select(Listing)))))
    engine.dispose()


if __name__ == "__main__":
    main()
