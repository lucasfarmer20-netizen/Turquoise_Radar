"""Render a synthetic combined page; no network, credentials or owner data."""

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_original_source_images import source_photo  # noqa: E402

from radar.db import DiscoveredItem  # noqa: E402
from radar.main import create_app  # noqa: E402

with tempfile.TemporaryDirectory(prefix="radar-release-browser-") as directory:
    root = Path(directory)
    with MonkeyPatch.context() as monkeypatch:
        monkeypatch.delenv("RADAR_EMAIL_REVIEW_DB", raising=False)
        with TestClient(create_app("sqlite:///" + str(root / "test.db"))) as client:
            candidate, _, _, _, _ = source_photo(client, root, monkeypatch)
            with client.app.state.sessions.begin() as session:
                row = session.get(DiscoveredItem, candidate)
                row.normalized = dict(
                    row.normalized,
                    item_end_time=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
                    buying_options=["AUCTION"],
                )
            Path(sys.argv[1]).write_text(client.get("/opportunities").text)
