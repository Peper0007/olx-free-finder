"""«Is the ad still online?» — re-checks open finds so removed ads leave «Находки» by themselves.

OLX answers 410 (removed) / 404 — its active pages also carry the text «Обявата не е активна»
(in a translations bundle), so for OLX only the status code counts. Bazar answers 404 or shows that text.
Anything unclear (network error, 403, captcha) counts as «don't know» and changes nothing.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .common import log, session

_GONE_TEXT = re.compile(r"Обявата не е активна|обявата е изтрита|обявата е изтекла", re.I)


def is_alive(url: str) -> bool | None:
    if not url:
        return None
    try:
        resp = session().get(url, timeout=20, allow_redirects=True)
    except Exception as exc:
        log.debug("alive check failed %s: %s", url, exc)
        return None
    if resp.status_code in (404, 410):
        return False
    if resp.status_code != 200:
        return None
    if "bazar.bg" in url and _GONE_TEXT.search(resp.text):
        return False
    return True


def check(items: list[dict], workers: int = 4) -> list[tuple[dict, bool | None]]:
    """items: [{id, url, ...}] → [(item, alive)]; a few in parallel, politely."""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(zip(items, pool.map(lambda it: is_alive(it["url"]), items)))
