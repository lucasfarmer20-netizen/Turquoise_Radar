"""Fixed rule identifiers and value-free Pydantic diagnostics for vision output."""

from pydantic import ValidationError
from pydantic_core import PydanticCustomError

RULES = {
    "vision_weight": ("NO_INVENTED_WEIGHT", "safety"),
    "vision_value": ("NO_INVENTED_VALUE", "safety"),
    "vision_culture": ("NO_CULTURAL_ATTRIBUTION", "safety"),
    "vision_treatment": ("NO_STONE_TREATMENT_CLAIM", "safety"),
    "vision_authentication": ("NO_AUTHENTICATION_CLAIM", "safety"),
    "vision_material": ("NO_VERIFIED_MATERIAL_CLAIM", "safety"),
    "vision_mine": ("NO_MINE_ATTRIBUTION", "safety"),
    "vision_maker_claim": ("NO_MAKER_AUTHENTICATION", "safety"),
    "vision_inferred_confidence": ("INFERRED_CONFIDENCE_LIMIT", "evidence"),
    "vision_hallmark_evidence": ("HALLMARK_DIRECT_LEGIBLE_METAL", "evidence"),
    "vision_hallmark_text": ("HALLMARK_CHARACTER_FORMAT", "formatting"),
    "vision_fragment_text": ("FRAGMENT_CHARACTER_FORMAT", "formatting"),
    "vision_duplicate_keys": ("UNIQUE_FINDING_AND_SUBITEM_KEYS", "references"),
    "vision_unknown_finding": ("KNOWN_FINDING_REFERENCE", "references"),
    "vision_maker_mark": ("MAKER_REQUIRES_LEGIBLE_MARK", "evidence"),
    "vision_generic_mark": ("GENERIC_MARK_NOT_MAKER_EVIDENCE", "evidence"),
    "vision_unknown_image": ("AUTHORIZED_PHOTO_REFERENCE", "references"),
    "vision_subitem_image": ("SUBITEM_EVIDENCE_SAME_PHOTO", "references"),
    "vision_incomplete": ("COMPLETE_STRUCTURED_RESPONSE", "formatting"),
}
FORMATS = {
    "missing": "REQUIRED_FIELD",
    "extra_forbidden": "STRICT_FIELDS",
    "literal_error": "ENUM_VALUE",
    "enum": "ENUM_VALUE",
    "string_too_long": "FIELD_LENGTH",
    "string_too_short": "FIELD_LENGTH",
    "too_long": "COLLECTION_LENGTH",
    "too_short": "COLLECTION_LENGTH",
    "string_pattern_mismatch": "FIELD_PATTERN",
    "json_invalid": "VALID_JSON",
    "json_type": "JSON_TYPE",
    "string_type": "FIELD_TYPE",
    "list_type": "FIELD_TYPE",
    "dict_type": "FIELD_TYPE",
    "model_type": "OBJECT_SHAPE",
    "model_attributes_type": "OBJECT_SHAPE",
    "none_required": "NULL_REQUIRED",
    "value_error": "UNCLASSIFIED_VALIDATION",
}
TOP_FIELDS = frozenset(
    {
        "classification",
        "summary",
        "visible_southwestern_style",
        "hallmark_candidate",
        "possible_maker_identity",
        "documented_cultural_attribution",
        "material_verification",
        "independent_owner_evidence",
        "findings",
        "sub_items",
    }
)
FINDING_FIELDS = frozenset(
    {
        "key",
        "category",
        "description",
        "image_id",
        "visible_evidence",
        "confidence",
        "evidence_kind",
        "uncertainty",
        "next_action",
        "signal",
        "legibility",
        "hallmark_transcription",
        "visible_fragments",
    }
)
SUBITEM_FIELDS = frozenset(
    {
        "key",
        "label",
        "image_id",
        "location_hint",
        "finding_keys",
        "uncertainty",
    }
)
MAKER_FIELDS = frozenset({"research_name", "supporting_finding_key", "uncertainty", "status"})


def rule_error(code):
    # Messages are fixed for normal exception handling too; diagnostics use only the code.
    if code not in RULES:
        raise ValueError("Unknown internal validation rule")
    return PydanticCustomError(code, "Vision output violates an evidence or format rule")


def reference_error(code, loc):
    return ValidationError.from_exception_data(
        "VisionResult",
        [
            {"type": rule_error(code), "loc": loc, "input": None},
        ],
    )


def safe_path(loc):
    """Recognize the schema's path grammar, never echo arbitrary object keys/union tags."""
    if not isinstance(loc, (tuple, list)):
        return ["$unknown"]
    if not loc:
        return ["$"]
    first, *tail = loc
    if not isinstance(first, str) or first not in TOP_FIELDS:
        return ["$unknown"]
    path = [first]
    if not tail:
        return path
    if first == "possible_maker_identity":
        return (
            path + tail
            if len(tail) == 1 and isinstance(tail[0], str) and tail[0] in MAKER_FIELDS
            else path + ["$unknown"]
        )
    if first not in {"findings", "sub_items"}:
        return path + ["$unknown"]
    index, *tail = tail
    if type(index) is not int or not 0 <= index < (20 if first == "findings" else 12):
        return path + ["$unknown"]
    path.append(index)
    if not tail:
        return path
    field, *tail = tail
    allowed = FINDING_FIELDS if first == "findings" else SUBITEM_FIELDS
    if not isinstance(field, str) or field not in allowed:
        return path + ["$unknown"]
    path.append(field)
    if not tail:
        return path
    limit = {"visible_fragments": 5, "finding_keys": 8}.get(field)
    if limit and len(tail) == 1 and type(tail[0]) is int and 0 <= tail[0] < limit:
        return path + tail
    return path + ["$unknown"]


def validation_diagnostics(error):
    if isinstance(error, PydanticCustomError):
        entries = [{"type": error.type, "loc": ()}]
    elif isinstance(error, ValidationError):
        # No input, context or documentation URL is requested. Never read or copy msg.
        entries = error.errors(include_input=False, include_context=False, include_url=False)
    else:
        return []
    result = []
    for entry in entries[:32]:
        code = entry.get("type")
        if not isinstance(code, str):
            code = "unknown_validation_type"
        if code in RULES:
            rule_id, kind = RULES[code]
        elif code in FORMATS:
            rule_id, kind = FORMATS[code], "formatting"
            if code == "value_error":
                kind = "unknown"
        else:
            code, rule_id, kind = "unknown_validation_type", "UNCLASSIFIED_VALIDATION", "unknown"
        path = safe_path(entry.get("loc"))
        if code == "literal_error" and path in (
            ["material_verification"],
            ["documented_cultural_attribution"],
            ["possible_maker_identity", "status"],
        ):
            rule_id, kind = "UNVERIFIED_STATUS_REQUIRED", "safety"
        if code == "literal_error" and path == ["independent_owner_evidence"]:
            rule_id, kind = "OWNER_EVIDENCE_NOT_MODEL_ASSESSED", "evidence"
        result.append(
            {
                "path": path,
                "validation_type": code,
                "rule_id": rule_id,
                "kind": kind,
            }
        )
    return result
