"""Free-stuff finder: OLX.bg (+ cheap categories) + Bazar.bg + Alo.bg → Gemini → Telegram (+ Firestore dashboard).

Usage:
  python main.py                 normal run (used by GitHub Actions)
  python main.py --dry-run       print results instead of sending to Telegram, don't save state
  python main.py --chat-id       print your TELEGRAM_CHAT_ID (write to the bot first)
  python main.py --test          send a test message to Telegram

Settings come from the dashboard (Firestore config/settings) when FIREBASE_SERVICE_ACCOUNT
is set; otherwise from env vars / built-in defaults (see finder/remote.py).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from finder import ai, alive, common, digest, geo, prefilter, remote, state, telegram
from finder.dedup import fingerprint, title_fingerprint
from finder.common import Ad, log
from finder.sources import alo, bazar, olx


def collect(settings: dict, seen: dict, report=lambda **kw: None) -> tuple[list[Ad], dict, list[str]]:
    """All sources in parallel; each one stops paging once it reaches ads we've already processed."""
    src = settings["sources"]
    cheap = src.get("olx_cheap") or {}
    plan = []
    if src["olx"].get("enabled", True):
        plan.append(("olx", lambda: olx.fetch(int(src["olx"].get("pages", 25)), seen)))
    if cheap.get("enabled") and cheap.get("categories"):
        plan.append(("olx_cheap", lambda: olx.fetch_cheap(cheap.get("categories"), float(cheap.get("max_eur", 5)),
                                                        int(cheap.get("pages", 3)), seen)))
    if src["bazar"].get("enabled", True):
        plan.append(("bazar", lambda: bazar.fetch(int(src["bazar"].get("pages", 10)), seen)))
    if src["alo"].get("enabled", True):
        plan.append(("alo", lambda: alo.fetch(src["alo"].get("queries") or None)))

    report(phase="collect", source=",".join(n for n, _ in plan), step=f"{len(plan)} сайта параллельно")

    def safe(item):
        name, fn = item
        try:
            return name, fn()
        except Exception as exc:
            log.exception("%s crashed: %s", name, exc)
            return name, []

    ads: list[Ad] = []
    counts: dict[str, int] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, len(plan))) as pool:
        for name, got in pool.map(safe, plan):
            counts[name] = len(got)
            if not got and name != "olx_cheap":
                failed.append(name)
            ads.extend(got)
    unique: dict[str, Ad] = {}
    for ad in ads:
        unique.setdefault(ad.key, ad)
    return list(unique.values()), counts, failed


def runs_left_today(now_utc: datetime) -> int:
    """Scheduled runs (hourly, see finder.yml) until the Gemini quota resets at midnight Pacific."""
    from zoneinfo import ZoneInfo
    pt = now_utc.astimezone(ZoneInfo("America/Los_Angeles"))
    return max(0, 23 - pt.hour)


def ai_allowance(settings: dict, run_id: str | None) -> tuple[int, int | None]:
    """How many Gemini requests this run may spend: the day's remaining quota spread evenly over the
    remaining hourly runs, so fresh ads in the evening still get checked. Returns (requests, left_today)."""
    rpd = int(settings.get("gemini_rpd") or 500)
    used = remote.gemini_used_today(exclude_run=run_id)
    if used is None:
        return 40, None
    left = max(0, rpd - used)
    share = left // (runs_left_today(datetime.now(timezone.utc)) + 1)
    return max(3 if left else 0, share), left


def check_alive(settings: dict) -> int:
    """Moves finds whose ad was removed from the site to «Забранные другими»."""
    if not settings.get("check_alive", True):
        return 0
    items = remote.load_open_finds(int(settings.get("min_score", 6)))
    gone = 0
    for item, alive_ in alive.check(items):
        if alive_ is None:
            continue
        remote.mark_checked(item["id"], alive_)
        if not alive_:
            gone += 1
            log.info("Removed from site → «Забранные другими»: %s", item["url"])
    if items:
        log.info("Alive check: %s checked, %s removed", len(items), gone)
    return gone


def run(dry_run: bool) -> int:
    settings = remote.load_settings()
    trigger = "manual" if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" else "schedule"
    if os.environ.get("GITHUB_EVENT_NAME") is None:
        trigger = "local"

    if settings.get("paused") and trigger == "schedule":
        log.info("Bot is paused in the dashboard — skipping scheduled run")
        return 0

    prefilter.configure(settings.get("skip_words"), settings.get("keep_words"), settings.get("skip_url_parts"))
    min_score = int(settings.get("min_score", 6))
    max_ai = int(settings.get("max_ai_per_run", 100))
    batch = max(1, min(6, int(settings.get("ai_batch", 4))))
    home = settings.get("home") or {}
    telegram_on = bool(settings.get("telegram_enabled", True)) and not dry_run

    run_id = None if dry_run else remote.start_run(trigger)
    stats: dict = {"status": "running", "errors": []}

    seen = state.load()
    first_run = not seen
    now = time.time()
    sent = analyzed = 0
    quota_left = None

    def report(force: bool = False, **fields):
        remote.progress(run_id, force=force, **fields)

    def on_fetch(url: str):
        m = re.search(r"[?&]page=(\d+)", url)
        src = "olx" if "olx.bg" in url else "bazar" if "bazar.bg" in url else "alo" if "alo.bg" in url else ""
        report(phase="collect", source=src, url=url, page=m.group(1) if m else "")

    common.on_fetch = on_fetch
    try:
        report(force=True, phase="start")
        ads, counts, failed = collect(settings, seen, report)
        common.on_fetch = None
        new = [a for a in ads if a.key not in seen]
        log.info("Collected %s ads, %s new%s", len(ads), len(new), " (first run)" if first_run else "")

        candidates: list[Ad] = []
        junk = dups = blocked = far = 0
        # items the owner marked spam / «уже забрали» / «забрал» are never sent again, even if reposted
        blocked_keys, blocked_fps, spam_titles = remote.load_blocked()
        batch_fps: set[str] = set()
        for ad in new:
            if prefilter.is_junk(ad):
                seen[ad.key] = now
                junk += 1
                continue
            fp = fingerprint(ad)
            if ad.key in blocked_keys or fp in blocked_fps or title_fingerprint(ad.title) in spam_titles:
                seen[ad.key] = now
                blocked += 1
                continue
            if fp in seen or fp in batch_fps:
                # same item already analyzed (reposted with a new id or listed on another site)
                seen[ad.key] = now
                dups += 1
                continue
            if home.get("only_near") and not geo.is_near(ad.location, home) and not geo.ships(ad):
                seen[ad.key] = now
                far += 1
                continue
            batch_fps.add(fp)
            candidates.append(ad)
        log.info("%s candidates after pre-filter (%s junk, %s duplicates, %s blocked by owner, %s far away)",
                 len(candidates), junk, dups, blocked, far)
        stats.update(counts=counts, collected=len(ads), new=len(new), junk=junk + far, far=far,
                     duplicates=dups + blocked, blocked=blocked, candidates=len(candidates), failed_sources=failed)

        # Free stuff goes fast: near-home / shippable ads first, then the freshest
        # (OLX has the exact time, Bazar says "днес").
        def priority(ad: Ad) -> tuple:
            fresh = ad.posted_at or (now - 3600 if "днес" in (ad.posted or "").lower() else 0.0)
            reachable = geo.is_near(ad.location, home) or geo.ships(ad)
            return (reachable, fresh)
        candidates.sort(key=priority, reverse=True)

        examples = remote.load_feedback_examples() if settings.get("learn_from_marks", True) else ""
        if examples:
            log.info("Using owner feedback examples (%s lines)", examples.count("\n- "))
        requests_allowed, quota_left = ai_allowance(settings, run_id)
        todo = candidates[:min(max_ai, requests_allowed * batch)]
        log.info("Gemini: %s requests allowed this run (quota left today: %s) → %s ads in batches of %s",
                 requests_allowed, quota_left, len(todo), batch)
        report(force=True, phase="filter", collected=len(ads), new=len(new), junk=junk + far, duplicates=dups,
               candidates=len(candidates), todo=len(todo))

        # GitHub kills the job at 30 min; stop analyzing in time to save results and seen.json cleanly
        budget = float(os.environ.get("RUN_BUDGET_MIN", "22")) * 60
        stop = threading.Event()
        hint = geo.home_hint(home)

        def work(group: list[Ad]):
            if stop.is_set() or time.time() - now > budget:
                return group, None
            for ad in group:
                if ad.source == "bazar" and ad.needs_details:
                    bazar.enrich(ad)
            try:
                return group, ai.analyze_batch(group, model=settings.get("model"), criteria=settings.get("criteria"),
                                               examples=examples, home=hint)
            except ai.QuotaExhausted:
                stop.set()
                return group, "quota"

        groups = [todo[i:i + batch] for i in range(0, len(todo), batch)]
        done = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            for group, verdicts in pool.map(work, groups):
                if verdicts == "quota":
                    if "Дневной лимит Gemini исчерпан" not in stats["errors"]:
                        log.warning("Gemini daily quota exhausted; the rest waits for the next run")
                        stats["errors"].append("Дневной лимит Gemini исчерпан")
                    continue
                if verdicts is None:
                    if not stop.is_set() and "Время запуска вышло" not in " ".join(stats["errors"]):
                        log.warning("Time budget reached; the rest waits for the next run")
                        stats["errors"].append(f"Время запуска вышло — оценено {analyzed} из {len(todo)}, остальное в следующий раз")
                    continue
                for ad, verdict in zip(group, verdicts):
                    done += 1
                    if verdict is None:
                        continue  # transient failure: retry next run
                    analyzed += 1
                    seen[ad.key] = now
                    seen[fingerprint(ad)] = now
                    score = verdict.get("score", 0)
                    log.info("[%s/10] %s — %s", score, ad.title, ad.url)
                    is_hit = score >= min_score
                    if is_hit:
                        if dry_run:
                            print("\n" + telegram.format_ad(ad, verdict) + "\n")
                        elif telegram_on:
                            try:
                                telegram.send_ad(ad, verdict, silent=score < int(settings.get("loud_score", 8)))
                                time.sleep(1)
                            except Exception as exc:
                                log.error("Telegram send failed: %s", exc)
                                stats["errors"].append(f"Telegram: {exc}")
                        sent += 1
                    if not dry_run:
                        remote.save_find(ad, verdict, sent=is_hit and telegram_on, run_id=run_id)
                report(phase="analyze", done=done, todo=len(todo), source=group[-1].source,
                       title=group[-1].title[:120], url=group[-1].url, hits=sent, calls=ai.calls)

        if first_run:
            for ad in candidates:
                seen.setdefault(ad.key, now)

        if not dry_run:
            stats["gone_checked"] = check_alive(settings)

        if len(failed) == len(counts) and counts and telegram_on:
            telegram.send_text("⚠️ Поиск бесплатных объявлений: ни один сайт не ответил. "
                               "Возможно, блокировка или сайты изменили вёрстку — проверь логи в GitHub Actions.")
        elif failed:
            log.warning("No ads from: %s", ", ".join(failed))
        stats["status"] = "ok"
    except Exception as exc:
        stats["status"] = "failed"
        stats["errors"].append(f"{type(exc).__name__}: {exc}"[:300])
        raise
    finally:
        if not dry_run:
            state.save(seen)
        backlog = max(0, stats.get("candidates", 0) - analyzed)
        stats.update(analyzed=analyzed, sent=sent, ai_calls=ai.calls, model=settings.get("model"), backlog=backlog)
        remote.finish_run(run_id, stats)
        log.info("Done: analyzed %s, sent %s, Gemini requests %s", analyzed, sent, ai.calls)
        if telegram_on and stats["status"] == "ok":
            left = None if quota_left is None else max(0, quota_left - ai.calls)
            digest.maybe_send(settings, backlog, left)
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
        telegram.send_text("✅ Бот работает. Находки будут приходить сюда каждый час.")
        return 0
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
