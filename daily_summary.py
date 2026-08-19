#!/usr/bin/env python3
"""
Gammill Watcher - Daily Summary
---------------------------------
Runs once a day (see .github/workflows/daily_summary.yml). Reads whatever
new listings the hourly watch_gammill.py found today (daily_summary.json)
and sends ONE confirmation email - either a recap of what was found, or an
explicit "nothing new today, still watching" message so you know the
watcher is alive even on quiet days.

After sending, it clears the daily log so tomorrow starts fresh.

Uses the same GitHub Secrets as watch_gammill.py:
    GMAIL_EMAIL, GMAIL_APP_PASSWORD, NOTIFY_EMAIL
"""

import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

GMAIL_EMAIL = os.environ["GMAIL_EMAIL"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_EMAIL)

DAILY_FILE = Path(__file__).parent / "daily_summary.json"


def format_listing(listing):
    is_retrofit = (listing.get("retrofit") or "").strip().lower() == "true" \
        or "retrofit" in (listing.get("category") or "").lower() \
        or "retrofit" in (listing.get("note") or "").lower()
    kind = "Retrofit" if is_retrofit else "Pre-Loved"
    return (
        f"[{kind}] {listing.get('year', '?')} Gammill {listing.get('family', '').title()}\n"
        f"  Throat: {listing.get('throat', '?')}\"\n"
        f"  Table: {listing.get('table', '?')}\n"
        f"  Price: ${listing.get('price', '?')}\n"
        f"  ID: {listing.get('id', '?')}"
    )


def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_EMAIL
    msg["To"] = NOTIFY_EMAIL

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_EMAIL, [NOTIFY_EMAIL], msg.as_string())


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entries = []
    if DAILY_FILE.exists():
        entries = json.loads(DAILY_FILE.read_text())

    if entries:
        subject = f"Gammill daily summary: {len(entries)} new listing(s) today"
        body = (
            f"The watcher found {len(entries)} new listing(s) today "
            "(you should have already gotten individual alerts for these):\n\n"
            + "\n\n".join(format_listing(e) for e in entries)
        )
    else:
        subject = "Gammill daily summary: no new listings today"
        body = (
            "No new pre-loved or retrofit listings today - the watcher "
            "checked hourly and everything's running normally.\n\n"
            "Full page: https://gammill.com/preloved/"
        )

    print(f"Sending daily summary ({len(entries)} entries today).")
    send_email(subject, body)

    # Reset for tomorrow
    DAILY_FILE.write_text("[]")


if __name__ == "__main__":
    main()
