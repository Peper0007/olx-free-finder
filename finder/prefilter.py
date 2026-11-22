"""Cheap keyword pre-filter: drops obvious junk before spending AI quota.

Edit SKIP_WORDS freely. Matching is case-insensitive on the title only
(descriptions often mention unrelated words).
"""
from __future__ import annotations

import re

from .common import Ad

# Animals, food, building materials, bulky furniture, baby gear, etc.
SKIP_WORDS = [
    # animals
    r"кот(е|ет|енц|к|ешк)", r"куч(е|ет|енц)", r"кученц", r"\bпале\b", r"зайч", r"папаг", r"хамстер",
    r"рибк", r"охлюв", r"кокошк", r"\bпиле", r"прасе", r"\bагне", r"осинов", r"търс\w* (\w+ )?дом",
    r"\bза[ий]\w*ц[иа]?\b|\bзаек", r"\bкуч\w*", r"котара?к", r"мачк", r"\bдом за\b", r"стопанин",
    r"намерен", r"изгубен", r"хъски", r"овчарк", r"мъниче",
    # food, plants, soil, firewood
    r"почва", r"\bтор\b", r"дърва", r"разсад", r"растени", r"храна за", r"плодове", r"зеленчуц",
    # building / bulky stuff
    r"керемид", r"тухл", r"дограм", r"прозор", r"врат[аи]\b", r"бойлер", r"радиатор", r"плочк",
    r"матрак", r"диван", r"легло", r"спалня", r"гардероб", r"секци[яи]", r"кухн[яи]", r"шкаф",
    r"холн", r"фотьойл", r"кошара", r"количк", r"столче за хранене", r"повивалник",
    r"хладилник", r"перална", r"съдомиял", r"плюшен", r"печка", r"фурна", r"бусове", r"гум[иа]\b", r"джант",
    # clothes for kids/babies, school books, medicines
    r"бебешк", r"детск[иа] дрех", r"дрешки", r"учебни", r"помагал", r"лекарств",
    # services / jobs / non-items
    r"квартир", r"нощувк", r"услуг", r"работа", r"извозва", r"кастрир",
]
_SKIP_RE = re.compile("|".join(SKIP_WORDS), re.I)

# Words that hint at collectible / vintage value: such titles always go to the AI,
# even if they also contain a "junk" word ("Зайчета от порцелан", "Ретро шкаф").
KEEP_WORDS = [
    r"порцелан", r"фигур", r"статуетк", r"керамик", r"бронз", r"кристал", r"стъкл", r"мед[ен]",
    r"ретро", r"винтидж", r"vintage", r"retro", r"антик", r"\bстар[иао]?\b", r"соц", r"ссср", r"колекци",
]
_KEEP_RE = re.compile("|".join(KEEP_WORDS), re.I)

# Parts of the ad URL that mark whole categories to skip (OLX: CID339 = animals, CID139 = kids/baby).
SKIP_URL_PARTS = ["CID339", "CID139"]
_SKIP_URL = list(SKIP_URL_PARTS)


def _compile(words: list[str]) -> re.Pattern | None:
    good = []
    for w in words or []:
        w = (w or "").strip()
        if not w:
            continue
        try:
            re.compile(w)
            good.append(w)
        except re.error:
            good.append(re.escape(w))  # treat broken regex as plain text
    return re.compile("|".join(good), re.I) if good else None


def configure(skip_words: list[str] | None = None, keep_words: list[str] | None = None,
              skip_url_parts: list[str] | None = None) -> None:
    """Replace the built-in lists (used when the dashboard provides its own)."""
    global _SKIP_RE, _KEEP_RE, _SKIP_URL
    if skip_url_parts is not None:
        _SKIP_URL = [p.strip() for p in skip_url_parts if p and p.strip()]
    if skip_words is not None:
        _SKIP_RE = _compile(skip_words)
    if keep_words is not None:
        _KEEP_RE = _compile(keep_words)


def is_junk(ad: Ad) -> bool:
    url = ad.url or ""
    if any(part in url for part in _SKIP_URL):
        return True
    title = ad.title or ""
    if _SKIP_RE is None or not _SKIP_RE.search(title):
        return False
    return not (_KEEP_RE is not None and _KEEP_RE.search(title))
