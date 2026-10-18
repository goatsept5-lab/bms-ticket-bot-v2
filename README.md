# bms-ticket-bot-v2

BookMyShow budget-seat (~₹54–67) alert bot for Chennai theatres. Sends a Telegram
message with a direct seat-layout link when a budget seat class opens, reopens,
or is still open 2 hours before showtime.

## What it watches

| Code | Theatre | Budget class (₹) | Budget rows |
|------|---------|------------------|-------------|
| INTO | INOX: The Marina Mall, OMR | EXECUTIVE 54–67 | N, O (front two) |
| PVHR | PVR: Heritage RSL ECR | CLASSIC 54–62 | A (front, 16 seats) |
| MCSK | Miraj Cinemas: Sekaran Mall | SPECIAL 54–59 | A (front, 16 seats) |
| CBMC | Cinepolis: BSR Mall, OMR | NORMAL 54–62 | A (front, 22 seats) |
| MAYJ | MAYAJAAL Multiplex: ECR | SECOND CLASS 58 | L (front, 13 seats) |
| ACON | AGS Cinemas OMR: Navalur (Vivira Mall) | PEARL 54–59 | T, U (front two, 34 seats) |
| TVHP | The Vijay Park Multiplex: Injambakkam | none (cheapest is ELITE 183) | – |

Rows were read from BMS seat maps on 2026-10-18 and can differ by screen.

## How it works

- `checker.py` checks every date each theatre has opened for booking and
  alerts on any seat class priced at or below `MAX_BUDGET_PRICE` (default 75).
- The same show is alerted again when it's bookable after being sold out or
  blocked by the theatre ("BOOKING OPEN AGAIN"), and once within 2 hours of
  showtime if budget seats are open ("SHOW IN 1h 40m").
- Telegram gets one message per theatre and date; every show block starts with
  the theatre name and links straight to its seat layout.
- `.github/workflows/bms_alert.yml` runs one long job that checks every 5 minutes
  for about 5h40m; an hourly cron queues the next one. (GitHub's own `*/5` cron
  fired only every 13–37 minutes.)
- `state.json` remembers what was already sent and only changes when alerts do.

## Notes

- Requests mirror the BMS website's own cinema-page call (`regionCode`/`appCode`
  params and `x-app-code`/`x-region-*` headers). The bare mobile call started
  getting HTTP 403 from GitHub runners on 2026-10-17.
- If BMS becomes unreachable for 3 checks in a row the bot sends one Telegram
  warning, then a "resumed" message when it recovers.
- Seat counts aren't included: BMS's seat map is served behind Cloudflare, which
  blocks headless browsers on GitHub runners.

## Secrets

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
