"""Profile completeness score, used by the partner reporting API."""


def completion(profile: dict, fields: list) -> dict:
    filled = sum(1 for f in fields if profile.get(f))
    percent = round(100 * filled / len(fields))
    return {"percent": percent, "filled": filled, "total": len(fields)}
