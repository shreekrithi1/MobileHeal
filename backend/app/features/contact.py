"""Contact card shown on the mobile apps' Profile screen (GET /api/profiles/{id}/contact)."""


def contact_card(profile: dict) -> dict:
    city = profile.get("city")
    phone = profile.get("phone_number") or ""
    return {"name": profile.get("name") or "", "email": profile.get("email") or "",
            "phone": ("•••• " + phone[-4:]) if phone else "", "city": city or ""}
