"""Free-stuff finder: OLX.bg + Bazar.bg + Alo.bg → Gemini → Telegram.

Usage:
  python main.py                 normal run (used by GitHub Actions)
  python main.py --dry-run       print results instead of sending to Telegram, don't save state
  python main.py --chat-id       print your TELEGRAM_CHAT_ID (write to the bot first)
  python main.py --test          send a test message to Telegram
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from finder import ai, state, telegram
from finder.common import Ad, log
from finder.prefilter import is_junk
from finder.sources import alo, bazar, olx


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def collect() -> tuple[list[Ad], list[str]]:
    ads: list[Ad] = []
    failed: list[str] = []
    for name, fn in (
        ("OLX", lambda: olx.fetch(env_int("OLX_PAGES", 5))),
        ("Bazar.bg", lambda: bazar.fetch(env_int("BAZAR_PAGES", 3))),
        ("Alo.bg", alo.fetch),
    ):
        try:
            got = fn()
        except Exception as exc:
            log.exception("%s crashed: %s", name, exc)
            got = []
        if not got:
            failed.append(name)
        ads.extend(got)
    # de-duplicate by key, keep first occurrence
    unique: dict[str, Ad] = {}
    for ad in ads:
        unique.setdefault(ad.key, ad)
    return list(unique.values()), failed


def run(dry_run: bool) -> int:
    min_score = env_int("MIN_SCORE", 6)
    max_ai = env_int("MAX_AI_PER_RUN", 45)

    seen = state.load()
    first_run = not seen
    now = time.time()

    ads, failed = collect()
    new = [a for a in ads if a.key not in seen]
    log.info("Collected %s ads, %s new%s", len(ads), len(new), " (first run)" if first_run else "")

    candidates: list[Ad] = []
    for ad in new:
        if is_junk(ad):
            seen[ad.key] = now
        else:
            candidates.append(ad)
    log.info("%s candidates after keyword pre-filter", len(candidates))

    sent = analyzed = 0
    try:
        for ad in candidates[:max_ai]:
            if ad.source == "bazar" and ad.needs_details:
                bazar.enrich(ad)
            try:
                verdict = ai.analyze(ad)
            except ai.QuotaExhausted:
                log.warning("Gemini daily quota exhausted; the rest waits for the next run")
                break
            analyzed += 1
            if verdict is None:
                continue  # transient failure: retry next run
            seen[ad.key] = now
            score = verdict.get("score", 0)
            log.info("[%s/10] %s — %s", score, ad.title, ad.url)
            if score >= min_score:
                if dry_run:
                    print("\n" + telegram.format_ad(ad, verdict) + "\n")
                else:
                    telegram.send_ad(ad, verdict)
                    time.sleep(1)
                sent += 1
        if first_run:
            # Don't drag the whole backlog through the AI over the next days.
            for ad in candidates:
                seen.setdefault(ad.key, now)
    finally:
        if not dry_run:
            state.save(seen)

    log.info("Done: analyzed %s, sent %s", analyzed, sent)
    if failed and not dry_run and len(failed) == 3:
        telegram.send_text("⚠️ Поиск бесплатных объявлений: ни один сайт не ответил. "
                           "Возможно, блокировка или сайты изменили вёрстку — проверь логи в GitHub Actions.")
    elif failed:
        log.warning("No ads from: %s", ", ".join(failed))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--chat-id", action="store_true")
    p.add_argument("--test", action="store_true")
    args = p.parse_args()

    if args.chat_id:
        telegram.print_chat_ids()
        return 0
    if args.test:
        telegram.send_text("✅ Бот работает. Находки будут приходить сюда 4 раза в день.")
        return 0
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
