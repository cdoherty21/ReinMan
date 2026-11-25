"""Normalize explicit race-card medication codes without inferring missing values."""
import re

_LABELS = {"L": "Lasix", "L1": "First-time Lasix", "O": "Off Lasix",
           "B": "Bute", "B1": "First-time Bute", "A": "Adjunct medication"}


def normalize(value):
    if not isinstance(value, str):
        return None
    code = re.sub(r"[\s{}^]", "", value).upper()
    return code or None


def label(value):
    code = normalize(value)
    if not code:
        return "Not listed"
    return _LABELS.get(code, f"Reported code {code}")


def first_lasix(current, previous):
    current, previous = normalize(current), normalize(previous)
    if current == "L1":
        return 1.0
    if current == "L" and previous in {"L", "L1", "O"}:
        return 0.0
    if current == "O":
        return 0.0
    return None
