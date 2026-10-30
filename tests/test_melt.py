from decimal import Decimal as D

import pytest

from radar.analysis import analyze
from radar.melt import calculate_melt, grams


def test_pennyweights():
    assert grams(D(10), "dwt") == D("15.55170")
    assert grams(D(10), "g") == 10


def reliable(item):
    return item.model_copy(
        update={
            "stated_weight": D(100),
            "weight_kind": "net_silver",
            "weight_verified": True,
            "weight_evidence": "Test scale",
            "material_evidence": "verified_sterling",
        }
    )


def test_melt_math(item, prefs):
    m = calculate_melt(reliable(item), prefs, D(45))
    assert m.theoretical_metal_value == D("89.22")
    assert m.conservative_liquidation_value == D("71.38")
    assert m.landed_to_melt_ratio == D(45) / D("71.38")
    assert m.eligible and m.confidence == "HIGH"


@pytest.mark.parametrize(
    "changes",
    [
        {"weight_kind": "gross"},
        {"weight_verified": False},
        {"material_evidence": "seller_claim"},
        {"material_evidence": "unknown"},
        {"material_evidence": "plated"},
        {"stated_weight": None},
    ],
)
def test_no_melt_arbitrage_without_evidence(item, prefs, changes):
    item = reliable(item).model_copy(update=changes)
    a = analyze(item, prefs)
    assert not a.melt.eligible
    assert a.melt.conservative_liquidation_value is None
    assert "STERLING / MELT" not in a.categories


def test_gross_upper_bound_warning(item, prefs):
    m = calculate_melt(reliable(item).model_copy(update={"weight_kind": "gross"}), prefs, D(20))
    assert m.theoretical_metal_value == D("89.22")
    assert "UPPER BOUND" in " ".join(m.warnings)
    assert m.conservative_liquidation_value is None


def test_no_fixed_spot(item, prefs):
    prefs.spot_usd_per_troy_oz = None
    assert calculate_melt(reliable(item), prefs).theoretical_metal_value is None


def test_shipping_above_twelve_can_work(item, prefs):
    a = analyze(
        reliable(item).model_copy(
            update={
                "item_price": D(30),
                "shipping_cost": D(15),
                "resale_low": None,
                "resale_high": None,
            }
        ),
        prefs,
    )
    assert a.tier == "BUY CANDIDATE" and "STERLING / MELT" in a.categories


def test_negative_keyword_does_not_discard_mixed_lot(item, prefs):
    a = analyze(
        item.model_copy(
            update={"raw_title": "Mixed sterling jewelry lot with plated clasp", "piece_count": 8}
        ),
        prefs,
    )
    assert a.tier == "BUY CANDIDATE"
    assert "MULTI-LOT" in a.categories
