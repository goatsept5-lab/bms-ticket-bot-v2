"""BookMyShow budget-seat alert bot for Chennai theatres.

Each run scans every bookable date at the target theatres and sends a Telegram
alert when a budget seat class (<= MAX_BUDGET_PRICE) opens. The same show is
alerted again:
  - when it's bookable again after being sold out or blocked by the theatre
  - once more within REMINDER_MINUTES of showtime, if budget seats are still open
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from curl_cffi import requests as bms_req

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Venue codes from BMS's Chennai cinema list
TARGET_THEATRES = {
    "INTO": "INOX: The Marina Mall, OMR",
    "PVHR": "PVR: Heritage RSL ECR",
    "MCSK": "Miraj Cinemas: Sekaran Mall",
    "TVHP": "The Vijay Park Multiplex: Injambakkam",
    "CBMC": "Cinepolis: BSR Mall, OMR",
    "MAYJ": "MAYAJAAL Multiplex: ECR",
    "ACON": "AGS Cinemas OMR: Navalur (Vivira Mall)",
}

MAX_BUDGET_PRICE = float(os.environ.get("MAX_BUDGET_PRICE", "75"))
REMINDER_MINUTES = int(os.environ.get("REMINDER_MINUTES", "120"))
MAX_DATES        = int(os.environ.get("MAX_DATES", "7"))
REOPEN_COOLDOWN  = timedelta(minutes=10)  # damps seats flickering between held and free
FAIL_ALERT_AFTER = 3
STATE_FILE       = "state.json"
DIVIDER          = "━━━━━━━━━━━━━━━━"

# Category AvailStatus, as checked against live seat maps:
# 3 = plenty of seats, 2 = filling fast, 1 = last few seats, 0 = sold out
AVAIL_LABELS = {"3": "Available", "2": "Filling fast", "1": "Almost full"}

# Same request the BMS website's cinema page makes
SHOWTIMES_URL   = ("https://in.bookmyshow.com/api/v3/mobile/showtimes/byvenue"
                   "?dateCode={date}&venueCode={venue}&regionCode=CHEN&memberId=&appCode=WEBV2&lsId=")
SEAT_LAYOUT_URL = "https://in.bookmyshow.com/movies/chennai/seat-layout/{event}/{venue}/{session}/{date}"

# Headers the BMS website sends with its API calls (User-Agent comes from curl_cffi's impersonation)
BMS_HEADERS = {
    "Referer":              "https://in.bookmyshow.com/",
    "Origin":               "https://in.bookmyshow.com",
    "Accept":               "application/json, text/plain, */*",
    "Accept-Language":      "en-IN,en;q=0.9",
    "x-app-code":           "WEB",
    "x-platform":           "WEB",
    "x-platform-code":      "WEB",
    "x-region-code":        "CHEN",
    "x-region-slug":        "chennai",
    "x-geohash":            "tf3",
    "x-latitude":           "13.056",
    "x-longitude":          "80.206",
    "x-location-selection": "manual",
}


def parse_bms_time(value):
    try:
        return datetime.strptime(value, "%Y%m%d%H%M").replace(tzinfo=IST)
    except (TypeError, ValueError):
        return None


def alerted_at(entry):
    try:
        ts = datetime.fromisoformat(entry.get("alerted_at", ""))
    except (TypeError, ValueError):
        return datetime(2000, 1, 1, tzinfo=IST)
    # Older entries were written as naive UTC timestamps by the runner
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def format_date(date_code):
    try:
        return datetime.strptime(date_code, "%Y%m%d").strftime("%a %d %b")
    except (TypeError, ValueError):
        return date_code


def load_state():
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError) as e:
            logging.error(f"State read failed, starting fresh: {e}")
    if not isinstance(state, dict):
        state = {}
    state.setdefault("alerts", {})
    state.setdefault("fail_streak", 0)
    state.pop("last_heartbeat", None)  # changed every run and forced a commit per run
    return state


def save_state(state, today_code):
    # Shows dated before today can't be booked any more
    state["alerts"] = {k: v for k, v in state["alerts"].items()
                       if isinstance(v, dict) and v.get("date", "") >= today_code}
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, STATE_FILE)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Telegram credentials missing!")
        return False
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    for attempt in range(1, 4):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code == 200 and body.get("ok"):
                return True
            if resp.status_code == 429:
                time.sleep(min(int(body.get("parameters", {}).get("retry_after", 5)), 30))
                continue
            logging.warning(f"Telegram attempt {attempt}: HTTP {resp.status_code} {body.get('description', '')}")
            if 400 <= resp.status_code < 500:
                return False  # bad token or chat id; retrying won't help
        except requests.RequestException as e:
            # Not logging the exception text: it can contain the URL, which holds the bot token
            logging.warning(f"Telegram attempt {attempt}: {type(e).__name__}")
        time.sleep(2 * attempt)
    return False


def fetch_showtimes(session, venue, date_code):
    url = SHOWTIMES_URL.format(date=date_code, venue=venue)
    for _ in range(2):
        try:
            r = session.get(url, headers=BMS_HEADERS, timeout=12)
            if r.status_code == 200:
                return r.json()
            logging.warning(f"{venue} {date_code}: HTTP {r.status_code}")
            if 400 <= r.status_code < 500:
                return None  # blocked or bad request; retrying won't help
        except Exception as e:
            logging.warning(f"{venue} {date_code}: {type(e).__name__}: {e}")
        time.sleep(2)
    return None


def iter_categories(data):
    for day in data.get("ShowDetails") or []:
        for ev in day.get("Event") or []:
            for child in ev.get("ChildEvents") or []:
                for show in child.get("ShowTimes") or []:
                    for cat in show.get("Categories") or []:
                        yield day, child, show, cat


def check_category(venue, day, child, show, cat, alerts, now, bookable):
    """Return an alert for this seat class, or None. Adds bookable budget classes to bookable."""
    try:
        price = float(cat.get("CurPrice") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 < price <= MAX_BUDGET_PRICE:
        return None
    session_id = show.get("SessionId")
    date_code  = show.get("ShowDateCode") or day.get("Date")
    if not session_id or not date_code:
        return None
    desc   = (cat.get("PriceDesc") or "").strip().upper()
    key    = f"{venue}_{session_id}_{date_code}_{desc}"
    status = str(cat.get("AvailStatus", "0"))
    if status not in AVAIL_LABELS:
        return None

    start  = parse_bms_time(show.get("ShowDateTime"))
    cutoff = parse_bms_time(show.get("CutOffDateTime")) or start
    if cutoff and cutoff < now:
        return None
    bookable.add(key)
    mins_to_start = (start - now).total_seconds() / 60 if start else None
    soon = mins_to_start is not None and 0 <= mins_to_start <= REMINDER_MINUTES

    prev = alerts.get(key)
    if prev is None:
        kind = "new"
    elif prev.get("sold_out") and now - alerted_at(prev) >= REOPEN_COOLDOWN:
        kind = "reopened"
    elif soon and not prev.get("soon_alerted"):
        kind = "soon"
    else:
        return None

    return {
        "key": key, "kind": kind, "soon": soon, "mins": mins_to_start,
        "venue": venue, "theatre": TARGET_THEATRES[venue],
        "movie": child.get("EventName") or "Movie", "date": date_code,
        "time": show.get("ShowTime") or "", "screen": show.get("ScreenName") or "",
        "category": desc, "price": price, "status": status, "start": start,
        "url": SEAT_LAYOUT_URL.format(event=child.get("EventCode"), venue=venue,
                                      session=session_id, date=date_code),
    }


def scan(session, state, now):
    """Check every bookable date at every theatre. Returns (hits, ok_requests, failed_requests)."""
    hits, keys, ok, failed = [], set(), 0, 0
    bookable, scanned = set(), set()
    for venue in TARGET_THEATRES:
        dates, seen = [now.strftime("%Y%m%d")], set()
        while dates and len(seen) < MAX_DATES:
            date_code = dates.pop(0)
            if date_code in seen:
                continue
            seen.add(date_code)
            data = fetch_showtimes(session, venue, date_code)
            time.sleep(0.5)
            if data is None:
                failed += 1
                continue
            ok += 1
            scanned.add((venue, date_code))
            scanned.update((venue, d.get("Date")) for d in data.get("ShowDetails") or [])
            if len(seen) == 1:
                # BMS lists the dates this venue has opened for booking
                dates += [d.get("DateCode") for d in data.get("ShowDatesArray") or []
                          if d.get("DateCode") and not d.get("isDisabled")]
            for day, child, show, cat in iter_categories(data):
                try:
                    hit = check_category(venue, day, child, show, cat, state["alerts"], now, bookable)
                except Exception as e:
                    logging.warning(f"{venue} {date_code}: skipped a seat class ({type(e).__name__}: {e})")
                    continue
                if hit and hit["key"] not in keys:
                    keys.add(hit["key"])
                    hits.append(hit)

    # An alerted class that's no longer bookable (sold out, or the theatre pulled
    # the show from sale) is alerted again if it opens up later
    for key, entry in state["alerts"].items():
        if (key.split("_")[0], entry.get("date")) in scanned and key not in bookable:
            entry["sold_out"] = True
    return hits, ok, failed


def format_hit(h):
    lines = [f"📍 {h['theatre']}"]
    if h["kind"] == "reopened":
        lines.append("🔁 BOOKING OPEN AGAIN")
    if h["soon"]:
        hours, mins = divmod(max(int(h["mins"]), 0), 60)
        lines.append(f"⏰ SHOW IN {hours}h {mins}m" if hours else f"⏰ SHOW IN {mins} MIN")
    lines.append(f"🎬 {h['movie']}")
    lines.append(f"🕘 {h['time']} · {format_date(h['date'])} · {h['screen']}")
    lines.append(f"💺 {h['category']} ₹{h['price']:.2f} · {AVAIL_LABELS[h['status']]}")
    lines.append(f"👉 {h['url']}")
    return "\n".join(lines)


def record_alert(state, h, now):
    prev = state["alerts"].get(h["key"], {})
    state["alerts"][h["key"]] = {
        "movie": h["movie"], "theatre": h["theatre"], "date": h["date"], "time": h["time"],
        "category": h["category"], "price": h["price"],
        "alerted_at": now.isoformat(timespec="seconds"),
        "sold_out": False, "soon_alerted": h["soon"] or prev.get("soon_alerted", False),
    }


def send_alerts(hits, state, now):
    """One Telegram message per theatre and date (split if long). Returns alerts delivered."""
    groups = {}
    for h in sorted(hits, key=lambda h: h["start"] or now):
        groups.setdefault((h["venue"], h["date"]), []).append(h)
    sent = 0
    for (venue, date_code), group in groups.items():
        chunks, current, size = [], [], 0
        for h in group:
            block = format_hit(h)
            if current and size + len(block) + len(DIVIDER) + 2 > 3600:
                chunks.append(current)
                current, size = [], 0
            current.append((h, block))
            size += len(block) + len(DIVIDER) + 2
        if current:
            chunks.append(current)
        for n, chunk in enumerate(chunks, 1):
            part = f" ({n}/{len(chunks)})" if len(chunks) > 1 else ""
            header = (f"🎟️ {TARGET_THEATRES[venue]}\n"
                      f"📅 {format_date(date_code)} · {len(group)} budget show(s){part}")
            body = f"\n{DIVIDER}\n".join(block for _, block in chunk)
            if send_telegram(f"{header}\n{DIVIDER}\n{body}"):
                for h, _ in chunk:
                    record_alert(state, h, now)
                sent += len(chunk)
            time.sleep(1)
    return sent


def check_health(state, ok, failed):
    """Warn on Telegram when BMS has been unreachable for several runs. Returns False on a failed run."""
    if ok == 0:
        # Capped so a long outage doesn't change state.json (and commit) every run
        state["fail_streak"] = min(state["fail_streak"] + 1, FAIL_ALERT_AFTER)
        if state["fail_streak"] == FAIL_ALERT_AFTER and not state.get("outage_alerted"):
            state["outage_alerted"] = True
            send_telegram(f"⚠️ BMS bot: BookMyShow unreachable for {FAIL_ALERT_AFTER} checks in a row "
                          f"({failed} failed requests). No alerts until it recovers.")
        return False
    if state.pop("outage_alerted", False):
        send_telegram("✅ BMS bot: BookMyShow is reachable again, alerts resumed.")
    state["fail_streak"] = 0
    return True


def main():
    now     = datetime.now(IST)
    state   = load_state()
    session = bms_req.Session(impersonate="chrome")
    try:
        hits, ok, failed = scan(session, state, now)
    finally:
        session.close()
    logging.info(f"BMS requests: {ok} ok, {failed} failed. Alerts to send: {len(hits)}")
    if hits:
        logging.info(f"Telegram alerts delivered: {send_alerts(hits, state, now)}/{len(hits)}")
    healthy = check_health(state, ok, failed)
    save_state(state, now.strftime("%Y%m%d"))
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
