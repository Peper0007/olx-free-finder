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


def is_junk(ad: Ad) -> bool:
    title = ad.title or ""
    return bool(_SKIP_RE.search(title)) and not _KEEP_RE.search(title)
