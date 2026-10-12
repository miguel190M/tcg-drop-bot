"""Google Calendar upsert (service account) + ICS fallback file."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os

log = logging.getLogger(__name__)

COLOR = {"BUY": "10", "WATCH": "5", "PASS": "8"}   # basil (green), banana, graphite
EMOJI = {"BUY": "🟢", "WATCH": "🟡", "PASS": "⛔"}
GAME = {"pokemon": "PKM", "onepiece": "OP"}


def build_events(release: dict, score) -> list[dict]:
    """One event per relevant date. Deterministic ids so reruns update, not duplicate."""
    tag = f"{EMOJI[score.tier]} [{GAME[release['game']]}] {release['name']}"
    desc = [f"FLIP verdict: {score.tier}  (flip {score.flip} / hold {score.hold}, confidence {score.confidence:.0%})"]
    if score.est_profit is not None:
        desc.append(f"Est. profit at RRP: A${score.est_profit:.0f}/unit after eBay fees + postage")
    desc += [f"• {r}" for r in score.reasons]
    if release.get("chase"):
        desc.append("Chase: " + ", ".join(release["chase"]))
    desc.append(f"Source: {release.get('source', '')}")
    if not release.get("confirmed", True):
        desc.append("⚠ Date not confirmed")
    body = "\n".join(desc)

    out = []
    for key, label in (("preorder_open", "Preorders open"), ("en_release", "EN release"),
                       ("jp_release", "JP release (watch prices)")):
        d = release.get(key)
        if not d:
            continue
        if isinstance(d, str):
            d = dt.date.fromisoformat(d)
        if key == "jp_release" and d < dt.date.today():
            continue
        uid = hashlib.sha1(f"{release['id']}|{key}".encode()).hexdigest()  # hex ⊂ base32hex: valid GCal id
        out.append({"id": uid, "summary": f"{tag} — {label}{'' if release.get('confirmed', True) else ' (TBC)'}",
                    "description": body, "date": d, "tier": score.tier,
                    "reminders": score.tier == "BUY"})
    return out


def write_ics(events: list[dict], path: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tcg-drop-bot//EN", "X-WR-CALNAME:TCG Drops",
             "CALSCALE:GREGORIAN"]

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    for e in events:
        d = e["date"]
        lines += ["BEGIN:VEVENT", f"UID:{e['id']}@tcg-drop-bot", f"DTSTAMP:{now}",
                  f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{esc(e['summary'])}", f"DESCRIPTION:{esc(e['description'])}"]
        if e["reminders"]:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", "TRIGGER:-P7D", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    with open(path, "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def sync_google(events: list[dict]) -> int:
    """Needs GOOGLE_SERVICE_ACCOUNT_JSON + GOOGLE_CALENDAR_ID. Returns events written."""
    sa, cal_id = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), os.getenv("GOOGLE_CALENDAR_ID")
    if not sa or not cal_id:
        log.info("Google Calendar not configured — ICS only")
        return 0
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa), scopes=["https://www.googleapis.com/auth/calendar"])
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    n = 0
    for e in events:
        body = {"id": e["id"], "summary": e["summary"], "description": e["description"],
                "start": {"date": e["date"].isoformat()},
                "end": {"date": (e["date"] + dt.timedelta(days=1)).isoformat()},
                "colorId": COLOR[e["tier"]], "transparency": "transparent",
                "reminders": {"useDefault": False,
                              "overrides": [{"method": "popup", "minutes": 7 * 24 * 60},
                                            {"method": "popup", "minutes": 24 * 60}] if e["reminders"] else []}}
        try:
            svc.events().update(calendarId=cal_id, eventId=e["id"], body=body).execute()
        except HttpError as err:
            if err.resp.status == 404:
                svc.events().insert(calendarId=cal_id, body=body).execute()
            else:
                raise
        n += 1
    return n
