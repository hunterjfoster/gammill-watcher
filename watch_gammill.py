#!/usr/bin/env python3
"""
Gammill Pre-Loved / Retrofit Watcher
-------------------------------------
Reads the live data feed behind https://gammill.com/preloved/ (a published
Google Sheet, fetched as CSV) and emails you a digest of any NEW active
listings since the last check - pre-loved or retrofit, any model, any size.

Designed to run on a schedule via GitHub Actions (see .github/workflows/watch.yml)
so it works even when your computer is off. Can also be run manually or via
cron/Task Scheduler if you prefer - see README.md.

Configuration is read from environment variables (set as GitHub Secrets):
    GMAIL_EMAIL          - the Gmail account used to send the alert
    GMAIL_APP_PASSWORD   - Gmail app password (not your normal password)
    NOTIFY_EMAIL         - where the alert should be sent (can be the same as GMAIL_EMAIL)
"""

import csv
import io
import json
import os
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1x0S6gnlnMGPq0uXl2JhxdLIr86DzjIYiBG8Asg3kJPM/gviz/tq?tqx=out:csv&gid=0"
)

GMAIL_EMAIL = os.environ["GMAIL_EMAIL"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_EMAIL)

SEEN_FILE = Path(__file__).parent / "seen_listings.json"
DAILY_FILE = Path(__file__).parent / "daily_summary.json"

# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_listings():
    """Pull the CSV feed and return a list of row dicts, keyed by column name."""
    resp = requests.get(CSV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    listings = []
    for row in reader:
        if not row.get("id"):
            continue
        listings.append(row)
    return listings


def is_active(listing):
    return (listing.get("status") or "").strip().lower() == "active"


def is_retrofit(listing):
    retrofit_flag = (listing.get("retrofit") or "").strip().lower()
    category = (listing.get("category") or "").strip().lower()
    note = (listing.get("note") or "").strip().lower()
    return retrofit_flag == "true" or "retrofit" in category or "retrofit" in note


# ---------------------------------------------------------------------------
# Seen-listing tracking
# ---------------------------------------------------------------------------

def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


def append_to_daily_summary(new_listings):
    """Record today's new listings so the once-a-day summary email can report them
    (or report 'nothing today' if this stays empty)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if DAILY_FILE.exists():
        entries = json.loads(DAILY_FILE.read_text())
    else:
        entries = []

    for listing in new_listings:
        entries.append({"date": today, **listing})

    DAILY_FILE.write_text(json.dumps(entries, indent=2))


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def format_listing(listing):
    kind = "Retrofit" if is_retrofit(listing) else "Pre-Loved"
    return (
        f"[{kind}] {listing.get('year', '?')} Gammill {listing.get('family', '').title()}\n"
        f"  Throat: {listing.get('throat', '?')}\"\n"
        f"  Table: {listing.get('table', '?')}\n"
        f"  Price: ${listing.get('price', '?')}"
        f" ({listing.get('monthly', '?')}/mo)\n"
        f"  ID: {listing.get('id', '?')}\n"
        f"  Note: {listing.get('note') or listing.get('memo') or '(none)'}"
    )


def send_digest(new_listings):
    subject = f"Gammill: {len(new_listings)} new listing(s)"
    body = (
        "New listing(s) found on the Gammill Pre-Loved / Retrofit feed:\n\n"
        + "\n\n".join(format_listing(l) for l in new_listings)
        + "\n\nFull page: https://gammill.com/preloved/"
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_EMAIL
    msg["To"] = NOTIFY_EMAIL

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_EMAIL, [NOTIFY_EMAIL], msg.as_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    listings = fetch_listings()

    if not listings:
        print("No rows found in the feed - check CSV_URL is still correct.")
        return

    seen = load_seen()
    new_seen = set(seen)
    new_active_listings = []

    for listing in listings:
        key = listing["id"]
        if key in seen:
            continue
        new_seen.add(key)

        if is_active(listing):
            new_active_listings.append(listing)

    if new_active_listings:
        print(f"Found {len(new_active_listings)} new active listing(s). Sending email.")
        send_digest(new_active_listings)
        append_to_daily_summary(new_active_listings)
    else:
        print("No new active listings this run.")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
