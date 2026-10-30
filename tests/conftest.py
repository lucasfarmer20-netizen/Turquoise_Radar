from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from radar.main import create_app
from radar.schemas import ListingInput, Preferences


@pytest.fixture
def item():
    return ListingInput(
        source="test",
        source_listing_id="one",
        raw_title="Blue stone ring",
        item_price="15",
        buyer_premium="0",
        buyer_service_fee="0",
        sales_tax="0",
        shipping_cost="5",
        handling_fee="0",
        shipping_status="CONFIRMED_TO_ALASKA",
        resale_low="45",
        resale_high="60",
        resale_basis="Manual test scenario",
    )


@pytest.fixture
def prefs():
    return Preferences(
        spot_usd_per_troy_oz=Decimal("30"),
        spot_timestamp="2026-01-01T00:00:00Z",
        spot_source="TEST ONLY",
    )


@pytest.fixture
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as client:
        yield client
