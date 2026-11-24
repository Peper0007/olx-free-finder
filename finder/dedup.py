"""Content fingerprint to skip duplicates: the same item reposted with a new id,
or the same item listed on several sites."""
from __future__ import annotations

import hashlib
import re

from .common import Ad

# Words that don't describe the item itself
_NOISE = re.compile(
    r"\b(подарява(м|ме|т)?|подарява се|подаряват се|безплатно|давам|даваме|спешно|лично предаване|"
    r"само|за|на|и|с|от|в|бр|броя|продавам)\b",
    re.I,
)


def _norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = _NOISE.sub(" ", text)
    text = re.sub(r"[^\w]+", " ", text)
    return " ".join(sorted(set(w for w in text.split() if len(w) > 1)))


def _city(location: str) -> str:
    loc = (location or "").lower()
    m = re.search(r"(?:гр\.|с\.)\s*([\w\- ]+)", loc)
    city = m.group(1) if m else loc.split(",")[-1]
    return re.sub(r"[^\w]+", "", city.split(",")[0])


def title_fingerprint(title: str) -> str:
    """Title only, city ignored — spam is often reposted with the same text in other cities."""
    norm = _norm(title)
    return "tfp:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16] if norm else ""


def fingerprint(ad: Ad) -> str:
    base = _norm(ad.title) + "|" + _city(ad.location)
    return "fp:" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
