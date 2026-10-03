import os
import json
import logging
import requests
from curl_cffi import requests as bms_req
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

TARGET_THEATRES = {
    "INTO": {"name": "INOX: The Marina Mall, OMR",            "slug": "inox-the-marina-mall-omr"},
    "PVHR": {"name": "PVR: Heritage RSL ECR",                 "slug": "pvr-heritage-rsl-ecr-chennai"},
    "MCSK": {"name": "Miraj Cinemas: Sekaran Mall",           "slug": "miraj-cinemas-sekaran-mall-perrumbakkam"},
    "TVHP": {"name": "The Vijay Park Multiplex: Injambakkam", "slug": "the-vijay-park-multiplex-injambakkam-ecr-4k-atmos"},
    "CBMC": {"name": "Cinepolis: BSR Mall, OMR",              "slug": "cinepolis-bsr-mall-omr-thoraipakkam"},
}

MAX_BUDGET_PRICE  = 75.0
BUDGET_CATEGORIES = {"EXECUTIVE","CLASSIC","SPECIAL","NORMAL","BUDGET","SECOND CLASS","FRONT ROW"}
STATE_FILE        = "state.json"

BMS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://in.bookmyshow.com/",
    "Origin":     "https://in.bookmyshow.com",
    "Accept":     "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
}

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"State read failed: {e}")
    return {"alerts": {}, "last_heartbeat": ""}

def save_state(state):
    try:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        state["alerts"] = {k: v for k, v in state.get("alerts", {}).items()
                           if isinstance(v, dict) and v.get("alerted_at", "") > cutoff}
        state["last_heartbeat"] = datetime.now().isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logging.error(f"State save failed: {e}")

def send_telegram(movie_name, theatre_name, show_date, show_time,
                  screen_name, category, price, booking_url, status_desc):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Telegram credentials missing!")
        return False
    try:
        formatted_date = datetime.strptime(show_date, "%Y%m%d").strftime("%a, %d %b %Y")
    except Exception:
        formatted_date = show_date
    message = (
        f"BUDGET TICKET ALERT (Rs60 TICKET OPEN!)\n\n"
        f"Movie: {movie_name}\n"
        f"Theatre: {theatre_name}\n"
        f"Date: {formatted_date}\n"
        f"Time: {show_time} ({screen_name})\n"
        f"Class: {category} (Base: Rs{price:.2f})\n"
        f"Status: {status_desc}\n\n"
        f"Seats go super fast! Book immediately:\n"
        f"{booking_url}"
    )
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 200:
                logging.info(f"Telegram sent: {movie_name} @ {theatre_name}")
                return True
            logging.warning(f"Attempt {attempt+1}: {resp.status_code} {resp.text}")
        except Exception as e:
            logging.warning(f"Attempt {attempt+1} error: {e}")
    return False

def check_tickets():
    state  = load_state()
    alerts = state.get("alerts", {})
    now    = datetime.now()
    dates  = [(now + timedelta(days=i)).strftime("%Y%m%d") for i in range(3)]
    new_alerts = 0

    for date_code in dates:
        logging.info(f"--- Checking {date_code} ---")
        for venue_code, info in TARGET_THEATRES.items():
            url = (f"https://in.bookmyshow.com/api/v3/mobile/showtimes/byvenue"
                   f"?dateCode={date_code}&venueCode={venue_code}")
            try:
                r = bms_req.get(url, headers=BMS_HEADERS, timeout=12, impersonate="chrome120")
                if r.status_code != 200:
                    logging.warning(f"{venue_code} HTTP {r.status_code}")
                    continue
                data = r.json()
                show_details = data.get("ShowDetails", [])
                if not show_details:
                    continue
                for ev in show_details[0].get("Event", []):
                    for cev in ev.get("ChildEvents", []):
                        movie_name = cev.get("EventName", "Movie")
                        for st in cev.get("ShowTimes", []):
                            session_id  = st.get("SessionId")
                            show_time   = st.get("ShowTime")
                            screen_name = st.get("ScreenName", "Main Audi")
                            for cat in st.get("Categories", []):
                                desc         = cat.get("PriceDesc", "").strip().upper()
                                price        = float(cat.get("CurPrice", 0))
                                avail_status = str(cat.get("AvailStatus", "0"))
                                is_budget    = (price <= MAX_BUDGET_PRICE) or (desc in BUDGET_CATEGORIES and price <= 100.0)
                                is_available = avail_status in {"1","2","3"}
                                if is_budget and is_available:
                                    label     = "Available" if avail_status in {"1","3"} else "Fast Filling"
                                    alert_key = f"{venue_code}_{session_id}_{date_code}_{desc}"
                                    if alert_key not in alerts:
                                        booking_url = (f"https://in.bookmyshow.com/cinemas/chennai/"
                                                       f"{info['slug']}/buytickets/{venue_code}/{date_code}")
                                        if send_telegram(movie_name, info["name"], date_code,
                                                         show_time, screen_name, desc,
                                                         price, booking_url, label):
                                            alerts[alert_key] = {
                                                "movie": movie_name, "theatre": info["name"],
                                                "date": date_code, "time": show_time,
                                                "category": desc, "price": price,
                                                "alerted_at": datetime.now().isoformat()
                                            }
                                            new_alerts += 1
                                    else:
                                        logging.debug(f"Already alerted: {alert_key}")
            except Exception as ex:
                logging.error(f"Error @ {venue_code}: {ex}")

    state["alerts"] = alerts
    save_state(state)
    logging.info(f"Done. New alerts sent: {new_alerts}")

if __name__ == "__main__":
    check_tickets()
