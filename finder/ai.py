"""Gemini vision filter: looks at photos + text and decides whether an ad
contains something worth reselling on Etsy / eBay."""
from __future__ import annotations

import base64
import json
import os
import time

import requests

from .common import Ad, get, log

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-flash-latest"

PROMPT = """Ты — опытный перекупщик винтажа из Болгарии. Продаёшь на Etsy и eBay.
Перед тобой бесплатное объявление («подарявам») с болгарского сайта. Посмотри на ВСЕ фото и текст.

Найди вещи, которые реально можно перепродать на Etsy/eBay за деньги, оправдывающие доставку:
винтаж и антиквариат (20+ лет), соц/советские предметы, фарфор, хрусталь, керамика, бижутерия, часы,
янтарь/бакелит, значки, монеты, марки, открытки, старые фото, книги/журналы/мануалы, плакаты, эмалированные
таблички, ретро-техника (фотоаппараты, радио, ресиверы, винил, кассеты), игрушки, предметы декора,
брендовые или коллекционные вещи, необычные инструменты, иконы, текстиль ручной работы и т.п.

НЕ интересно: животные, обычная мебель и техника, обычная одежда и обувь, детские вещи, стройматериалы,
еда, растения, массовые дешёвые вещи, громоздкие предметы без коллекционной ценности.

Объявление:
Сайт: {source}
Заголовок: {title}
Место: {location}
Доставка: {delivery}
Описание: {description}

Ответь строго JSON-объектом:
{{
  "score": целое 0-10 (10 = точно стоит забирать ради Etsy/eBay, 0 = мусор),
  "items": "что именно ценного видно (по-русски, коротко)",
  "why": "почему это продаётся, 1 предложение (по-русски)",
  "price": "реалистичная цена продажи на Etsy/eBay, например '$20–40' или '€15 за набор'",
  "pickup": "легко ли забрать: 'отправит курьером' / 'только лично' / 'неизвестно'"
}}"""

_last_call = 0.0


def _image_part(url: str) -> dict | None:
    try:
        resp = get(url, pause=0.3, retries=1)
    except Exception as exc:
        log.debug("image download failed %s: %s", url, exc)
        return None
    mime = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    if not mime.startswith("image/") or len(resp.content) > 4_000_000:
        return None
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(resp.content).decode()}}


def analyze(ad: Ad, *, min_interval: float = 7.0) -> dict | None:
    """Returns the parsed JSON verdict, or None on failure."""
    global _last_call
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    model = os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL

    parts: list[dict] = [
        {
            "text": PROMPT.format(
                source=ad.source,
                title=ad.title,
                location=ad.location or "?",
                delivery=ad.delivery or "?",
                description=(ad.description or "—")[:1500],
            )
        }
    ]
    for url in ad.photos[:4]:
        part = _image_part(url)
        if part:
            parts.append(part)

    body = {
        "contents": [{"role": "user", "parts": parts}],
        # No temperature/top_p: deprecated for current Gemini models.
        "generationConfig": {"responseMimeType": "application/json"},
    }

    # Respect free-tier requests-per-minute limits.
    wait = min_interval - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)

    for attempt in range(3):
        _last_call = time.time()
        resp = requests.post(
            API_URL.format(model=model),
            params={"key": api_key},
            json=body,
            timeout=90,
        )
        if resp.status_code == 429:
            if "PerDay" in resp.text or "per day" in resp.text.lower():
                raise QuotaExhausted("Gemini daily quota exhausted")
            time.sleep(30 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            time.sleep(10)
            continue
        if resp.status_code != 200:
            log.error("Gemini error %s: %s", resp.status_code, resp.text[:500])
            if resp.status_code in (400, 403, 404):
                raise RuntimeError(f"Gemini request rejected ({resp.status_code}); check model/key")
            return None
        try:
            parts_out = resp.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts_out if not p.get("thought"))
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(text)
            if isinstance(data, list):
                data = data[0] if data else {}
            data["score"] = int(data.get("score", 0))
            return data
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            log.warning("Gemini answer not parsed for %s: %s", ad.key, exc)
            return None
    return None


class QuotaExhausted(RuntimeError):
    pass
