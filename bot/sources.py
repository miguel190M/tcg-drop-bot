"""Auto-discovery of new sets. Best-effort: any source can fail without
breaking the run. Discovered sets are flagged `needs_review` and alerted on
Telegram so you can add chase/RRP details to data/releases.yaml.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re

import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "tcg-drop-bot/1.0 (personal release tracker)"}


def pokemontcgio_sets(since: dt.date) -> list[dict]:
    """Sets listed on pokemontcg.io with a release date on/after `since`."""
    headers = dict(UA)
    if os.getenv("POKEMONTCG_API_KEY"):
        headers["X-Api-Key"] = os.environ["POKEMONTCG_API_KEY"]
    r = requests.get("https://api.pokemontcg.io/v2/sets",
                     params={"orderBy": "-releaseDate", "pageSize": 15}, headers=headers, timeout=30)
    r.raise_for_status()
    out = []
    for s in r.json().get("data", []):
        d = dt.datetime.strptime(s["releaseDate"], "%Y/%m/%d").date()
        if d < since:
            continue
        name = s["name"]
        set_type = "special" if re.search(r"\b(151|prismatic|crown zenith|celebrat|paldean fates|shrouded)", name, re.I) else "main"
        out.append({"id": f"pkm-{s['id']}", "game": "pokemon", "name": name, "code": s["id"].upper(),
                    "set_type": set_type, "en_release": d, "confirmed": True, "chase": [],
                    "reprint_risk": "medium", "source": "pokemontcg.io", "needs_review": True})
    return out


_OP_CODE = re.compile(r"\b(OP|EB|PRB)-?(\d{2})\b")
_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d\d)")


def onepiece_official(since: dt.date) -> list[dict]:
    """Scrapes the official EN product list for booster codes with dates.
    Pattern-based on visible text, so it survives most layout changes."""
    r = requests.get("https://en.onepiece-cardgame.com/products/", headers=UA, timeout=30)
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"\s+", " ", text)
    out, seen = [], set()
    for m in _OP_CODE.finditer(text):
        code = f"{m.group(1)}-{m.group(2)}"
        if code in seen:
            continue
        window = text[m.end(): m.end() + 400]
        dm = _DATE.search(window)
        if not dm:
            continue
        d = dt.datetime.strptime(f"{dm.group(1)} {dm.group(2)} {dm.group(3)}", "%B %d %Y").date()
        if d < since:
            continue
        seen.add(code)
        set_type = {"OP": "main", "EB": "extra", "PRB": "premium"}[m.group(1)]
        out.append({"id": f"op-{code.lower().replace('-', '')}", "game": "onepiece", "name": code, "code": code,
                    "set_type": set_type, "en_release": d, "confirmed": True, "chase": [],
                    "reprint_risk": "medium", "source": "en.onepiece-cardgame.com", "needs_review": True})
    return out


def discover(since: dt.date) -> list[dict]:
    found = []
    for fn in (pokemontcgio_sets, onepiece_official):
        try:
            found += fn(since)
        except Exception as e:  # noqa: BLE001 — a dead source must not kill the run
            log.warning("source %s failed: %s", fn.__name__, e)
    return found


def merge(manual: list[dict], discovered: list[dict]) -> list[dict]:
    """Manual entries win. A discovered set matching a manual code/name is dropped."""
    keys = set()
    for r in manual:
        keys.add(r["id"])
        if r.get("code"):
            keys.add(r["code"].upper())
        keys.add(r["name"].lower())
    manual_names = [r["name"].lower() for r in manual]
    merged = list(manual)
    for d in discovered:
        dn = d["name"].lower()
        if (d["id"] in keys or d["code"].upper() in keys or dn in keys
                or any(dn in mn or mn in dn for mn in manual_names)):
            continue
        merged.append(d)
    return merged
