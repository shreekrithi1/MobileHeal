"""Personalised greeting shown at the top of the mobile app."""


def make_greeting(profile: dict) -> dict:
    first = ((profile["name"] or "").split() or [""])[0]
    return {"greeting": f"Hi {first}", "first_name": first}
