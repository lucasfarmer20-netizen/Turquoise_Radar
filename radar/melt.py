from decimal import Decimal

from pydantic import BaseModel

from radar.finance import money
from radar.schemas import ListingInput, Preferences


class MeltResult(BaseModel):
    weight_g: Decimal | None = None
    theoretical_metal_value: Decimal | None = None
    conservative_liquidation_value: Decimal | None = None
    landed_to_melt_ratio: Decimal | None = None
    confidence: str = "LOW"
    eligible: bool = False
    warnings: list[str] = []


def grams(weight: Decimal, unit: str) -> Decimal:
    return weight * Decimal("1.55517") if unit == "dwt" else weight


def calculate_melt(item: ListingInput, prefs: Preferences, landed=None) -> MeltResult:
    result = MeltResult()
    if item.stated_weight is None:
        result.warnings.append("Unknown weight: no numerical melt estimate")
        return result
    result.weight_g = grams(item.stated_weight, item.weight_unit)
    if item.material_evidence == "plated":
        result.warnings.append("Plated material: sterling valuation is inapplicable")
        return result
    if prefs.spot_usd_per_troy_oz is None:
        result.warnings.append("No timestamped silver spot price configured")
        return result
    if item.material_evidence not in ("seller_claim", "verified_sterling"):
        result.warnings.append("No sterling evidence: no numerical sterling estimate")
        return result
    result.theoretical_metal_value = money(
        result.weight_g * Decimal("0.925") * prefs.spot_usd_per_troy_oz / Decimal("31.1034768")
    )
    if item.weight_kind == "gross":
        result.warnings.append(
            "THEORETICAL UPPER BOUND: gross weight includes stones and other material"
        )
        return result
    material_ok = item.material_evidence == "verified_sterling" or (
        not prefs.require_verified_material and item.material_evidence == "seller_claim"
    )
    if not item.weight_verified or not material_ok:
        result.warnings.append(
            "Net weight or material evidence does not meet confidence requirements"
        )
        return result
    result.confidence = "HIGH"
    result.conservative_liquidation_value = money(
        result.theoretical_metal_value * prefs.refiner_payout
    )
    if landed is not None and result.conservative_liquidation_value > 0:
        result.landed_to_melt_ratio = landed / result.conservative_liquidation_value
        result.eligible = result.landed_to_melt_ratio <= prefs.melt_max_ratio
    return result
