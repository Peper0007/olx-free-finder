"""Pickup area: is the ad near home, or will the seller ship it?"""
from __future__ import annotations

import re

from .common import Ad

_SHIPS = re.compile(r"изпращ|еконт|econt|спиди|speedy|куриер|с доставка|по куриер|наложен", re.I)
_ONLY_PICKUP = re.compile(r"само лично|лично предаване|само на място|без изпращане|не изпращам", re.I)


def _norm(s: str) -> str:
    return (s or "").lower().replace("гр.", " ").replace("с.", " ")


def is_near(location: str, home: dict) -> bool:
    loc = _norm(location)
    places = [home.get("city") or ""] + list(home.get("nearby") or [])
    return any(p and p.lower() in loc for p in places)


def ships(ad: Ad) -> bool:
    text = f"{ad.title} {ad.description}"
    if _ONLY_PICKUP.search(text):
        return False
    # OLX: the «who pays delivery» field exists only when the seller offers courier delivery
    return bool(ad.delivery) or bool(_SHIPS.search(text))


def home_hint(home: dict) -> str:
    city = home.get("city") or ""
    if not city:
        return ""
    return (f"Владелец живёт в городе {city} и может забрать вещь лично только там и рядом "
            f"({', '.join(home.get('nearby') or [])}). Если вещь далеко и продавец не отправляет курьером, "
            "снижай оценку на 1–2 балла: ехать за ней обычно не окупается.")
