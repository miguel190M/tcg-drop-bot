"""Daily run: discover → score → calendar → Telegram → save state.

python -m bot.main            normal run
python -m bot.main --offline  no network (seed data only), prints Telegram output
python -m bot.main --digest   force the weekly digest
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import os
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import yaml

from . import calendar_sync, ebay, landed, sources, telegram
from .scoring import score_release

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STATE = os.path.join(DATA, "state.json")
OUTCOMES = os.path.join(DATA, "outcomes.json")
ICS = os.path.join(DATA, "releases.ics")
OUTCOME_CHECKPOINTS = [14, 90, 365]   # days after EN release to log market price

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("bot")


def _load_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _as_date(v) -> dt.date | None:
    if v is None or isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def h(s) -> str:
    return html.escape(str(s))


VERDICT_LINE = {"BUY": "🟢 <b>BUY</b> — flip it", "WATCH": "🟡 <b>WATCH</b> — only at RRP, if easy",
                "PASS": "⛔ <b>PASS</b> — don't buy to flip"}


def retail_query(r: dict) -> str:
    if r.get("retail_query"):
        return r["retail_query"]
    if r["game"] == "onepiece":
        return f"one piece {r['code']}" if r.get("code") else f"one piece {r['name']}"
    return "pokemon " + r["name"].split("—")[-1].split("(")[0].strip()


def where_to_buy(r: dict, cfg: dict) -> str:
    q = quote_plus(retail_query(r))
    stores = (cfg.get("local_game_stores") or []) + (cfg.get("where_to_buy") or [])
    links = [f'<a href="{h(st["url"].format(q=q))}">{h(st["name"])}</a>' for st in stores]
    return " · ".join(links)


def card(header: str, r: dict, s, cfg: dict, today: dt.date) -> str:
    game = "Pokémon" if r["game"] == "pokemon" else "One Piece"
    code = f" {r['code']}" if r.get("code") else ""
    lines = [header, VERDICT_LINE[s.tier], f"<b>{h(r['name'])}</b> ({game}{h(code)})"]
    dates = f"EN {r['en_release']:%a %d %b}" + ("" if r.get("confirmed", True) else " (TBC)")
    if r.get("preorder_open") and r["preorder_open"] >= today:
        dates += f" · preorders open {r['preorder_open']:%a %d %b}"
    lines.append(dates)
    if s.est_profit is not None:
        lines.append(f"Est. profit at RRP: <b>A${s.est_profit:.0f}/unit</b> after eBay fees + postage")
    lines.append(f"Flip {s.flip}/100 · confidence {s.confidence:.0%}")
    if s.reasons:
        lines.append("Why: " + "; ".join(h(x) for x in s.reasons))
    if s.tier != "PASS":
        tip = "Preorder at your LGS first — " if s.tier == "BUY" else ""
        lines.append(f"Where: {tip}{where_to_buy(r, cfg)}")
    return "\n".join(lines)


def run(offline: bool = False, force_digest: bool = False) -> None:
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    with open(os.path.join(DATA, "releases.yaml")) as f:
        manual = yaml.safe_load(f)["releases"]

    today = dt.datetime.now(ZoneInfo(cfg["timezone"])).date()
    state = _load_json(STATE, {"releases": {}})
    outcomes = _load_json(OUTCOMES, {})
    alerts: list[str] = []

    discovered = [] if offline else sources.discover(today - dt.timedelta(days=7))
    releases = sources.merge(manual, discovered)
    for r in releases:
        for k in ("en_release", "jp_release", "preorder_open"):
            r[k] = _as_date(r.get(k))

    all_events, scored = [], []
    for r in releases:
        if not r.get("en_release"):
            continue
        days_out = (r["en_release"] - today).days

        # Outcome tracking for released sets (feeds future weight tuning)
        if days_out < 0:
            age = -days_out
            for cp in OUTCOME_CHECKPOINTS:
                key = f"{r['id']}|{cp}"
                if age >= cp and key not in outcomes and not offline and r.get("ebay_query"):
                    p = ebay.market_price_aud(r["ebay_query"])
                    if p:
                        prev = state["releases"].get(r["id"], {})
                        outcomes[key] = {"date": today.isoformat(), "price_aud": p,
                                         "tier_at_release": prev.get("tier"), "score": prev.get("combined")}
            if age > 30:
                continue

        market = None if offline else ebay.market_price_aud(r.get("ebay_query", ""))
        s = score_release(r, cfg, market)
        scored.append((r, s, market, days_out))
        all_events += calendar_sync.build_events(r, s)

        prev = state["releases"].get(r["id"])
        label = f"<b>{h(r['name'])}</b> ({'Pokémon' if r['game'] == 'pokemon' else 'One Piece'})"
        if prev is None:
            alerts.append(card("🆕 New on calendar", r, s, cfg, today)
                          + ("\n<i>needs review — add chase/RRP to releases.yaml</i>" if r.get("needs_review") else ""))
        elif prev.get("tier") != s.tier:
            alerts.append(card(f"📈 Verdict change ({prev.get('tier')} → {s.tier})", r, s, cfg, today))
        if prev and prev.get("en_release") and prev["en_release"] != r["en_release"].isoformat():
            alerts.append(f"📅 Date moved: {label}\n{prev['en_release']} → {r['en_release']}")

        if s.tier != "PASS":
            for key, what in (("en_release", "releases"), ("preorder_open", "preorders open")):
                d = r.get(key)
                if d and (d - today).days in cfg["alerts"]["release_reminder_days"]:
                    alerts.append(card(f"⏰ {what.capitalize()} in {(d - today).days} day(s)", r, s, cfg, today))

        # Overseas landed-cost checks
        for offer in r.get("offers") or []:
            if offline or not market:
                continue
            try:
                lc = landed.landed_cost(offer, cfg)
            except Exception as e:  # noqa: BLE001
                log.warning("fx/landed failed for %s: %s", offer.get("store"), e)
                continue
            m = landed.resale_margin(lc["per_unit"], market, cfg)
            if m["margin_pct"] >= cfg["alerts"]["min_landed_margin_pct"]:
                alerts.append(f"🌏 Overseas buy: {label} @ {h(offer['store'])} ({offer.get('country', '')})\n"
                              f"Landed A${lc['per_unit']:.0f}/unit vs AU market A${market:.0f} → "
                              f"profit A${m['profit']:.0f} ({m['margin_pct']:.0f}%) after eBay fees + postage"
                              + (f"\n{h(offer['url'])}" if offer.get("url") else ""))

        state["releases"][r["id"]] = {"tier": s.tier, "combined": s.combined, "flip": s.flip, "hold": s.hold,
                                      "en_release": r["en_release"].isoformat(), "market_aud": market,
                                      "updated": today.isoformat()}

    calendar_sync.write_ics(all_events, ICS)
    if not offline:
        try:
            n = calendar_sync.sync_google(all_events)
            log.info("Google Calendar: %d events upserted", n)
        except Exception as e:  # noqa: BLE001
            alerts.append(f"⚠ Google Calendar sync failed: {h(e)}")

    if alerts:
        telegram.send("\n\n".join(alerts))

    if force_digest or today.weekday() == 0:   # Monday digest
        upcoming = sorted([x for x in scored if x[3] >= 0], key=lambda x: x[0]["en_release"])
        lines = ["<b>🗓 TCG drops — next 90 days</b>"]
        for r, s, market, days_out in upcoming:
            if days_out > 90:
                continue
            em = calendar_sync.EMOJI[s.tier]
            px = f" · est A${s.est_profit:.0f}/unit" if s.est_profit is not None else ""
            tbc = " (TBC)" if not r.get("confirmed", True) else ""
            why = f"\n   ↳ {h(s.reasons[0])}" if s.tier == "PASS" and s.reasons else ""
            where = f"\n   🛒 {where_to_buy(r, cfg)}" if s.tier != "PASS" else ""
            lines.append(f"{em} <b>{s.tier}</b> {h(r['name'])} [{calendar_sync.GAME[r['game']]}] — "
                         f"{r['en_release']:%d %b}{tbc} · flip {s.flip}{px}{why}{where}")
        if len(lines) == 1:
            lines.append("Nothing dated in the next 90 days.")
        telegram.send("\n".join(lines))

    if offline:   # dry run: don't persist, so the first live run still sends "new" alerts
        log.info("offline: %d releases scored, %d alerts (state not saved)", len(scored), len(alerts))
        return
    os.makedirs(DATA, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    with open(OUTCOMES, "w") as f:
        json.dump(outcomes, f, indent=2, sort_keys=True)
    log.info("done: %d releases scored, %d alerts", len(scored), len(alerts))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--digest", action="store_true")
    a = ap.parse_args()
    run(offline=a.offline, force_digest=a.digest)
