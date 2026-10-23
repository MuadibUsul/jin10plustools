from .fixed_brief_copy import render


def run(payload: dict) -> dict:
    return render("oil_etf", payload)
