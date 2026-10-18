# bms-ticket-bot-v2

BookMyShow budget-seat (~₹54–67) alert bot for Chennai theatres. Sends a Telegram
message when a budget seat class opens up, with free seats per row and a direct
seat-layout link.

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
- It alerts again when an alerted show starts within 15 minutes, or when a sold-out
  class gets seats back.
- Free seats per row come from BMS's accessibility seat panel, read in headless
  Chromium for the 12 soonest shows in each batch.
- `.github/workflows/bms_alert.yml` runs one long job that checks every 5 minutes
  for about 5h40m; an hourly cron queues the next one.
- `state.json` remembers what was already sent and only changes when alerts do.

## Known issue

Since 2026-10-17 ~04:03 UTC BookMyShow answers HTTP 403 to GitHub-hosted runners,
so no alerts can be sent from GitHub Actions. The bot sends one Telegram warning
when this happens and a "resumed" message if access comes back.

## Secrets

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
