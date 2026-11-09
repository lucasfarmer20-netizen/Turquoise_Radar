"""Shared identity matching; standard library only, no image dependencies."""

from radar.ebay_deletion.protocol import digest


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from dictionaries(v)
    elif isinstance(value, list):
        for v in value:
            yield from dictionaries(v)


def identity_state(values, identifiers, secret, aliases):
    stable, usernames = set(), set()
    for value in values:
        for part in dictionaries(value):
            seller = part.get("seller")
            if isinstance(seller, dict):
                for field in ("userId", "eiasToken"):
                    if isinstance(seller.get(field), str):
                        stable.add((field, digest(secret, field, seller[field])))
                if isinstance(seller.get("username"), str):
                    usernames.add(digest(secret, "username", seller["username"]))
            if isinstance(part.get("seller_id"), str):
                usernames.add(digest(secret, "username", part["seller_id"]))
    # Reviewed aliases are identity evidence, including when they contradict a direct ID.
    stable |= {("userId", aliases[name]) for name in usernames if name in aliases}
    return hashed_identity_state(stable, usernames, identifiers, aliases)


def hashed_identity_state(stable, usernames, identifiers, aliases):
    matches = {(kind, value) for kind, value in stable if identifiers.get(kind) == value}
    conflicts = {
        (kind, value)
        for kind, value in stable
        if kind in identifiers and identifiers[kind] != value
    }
    comparable = {kind for kind, _ in stable if kind in identifiers}
    if matches and conflicts:
        return "AMBIGUOUS"
    if matches:
        return "UNMAPPED" if any(kind not in identifiers for kind, _ in stable) else "MATCH"
    if identifiers.get("username") in usernames:
        return "AMBIGUOUS"
    if stable and any(kind not in comparable for kind, _ in stable):
        return "UNMAPPED"
    if usernames and not comparable and any(name not in aliases for name in usernames):
        return "UNMAPPED"
    return "NONE"
