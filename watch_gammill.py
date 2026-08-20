#!/usr/bin/env python3
"""
Gammill Pre-Loved / Retrofit Watcher
-------------------------------------
Reads the live data feed behind https://gammill.com/preloved/ (a published
Google Sheet, fetched as CSV) and:

  1. Emails you a digest of any NEW active listings since the last check -
     pre-loved or retrofit, any model, any size.
  2. Logs every "Listed" and "Sold" event, with a timestamp, to
     listing_log.csv - open that file any time in Excel/Google Sheets for
     a full history of what's come and gone.

Designed to run on a schedule via GitHub Actions (see .github/workflows/watch.yml)
so it works even when your computer is off.

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
LOG_FILE = Path(__file__).parent / "listing_log.csv"

LOG_HEADERS = [
    "timestamp_utc", "event", "id", "kind", "family",
    "year", "throat", "table", "price", "note",
]

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


def is_retrofit(listing):
    retrofit_flag = (listing.get("retrofit") or "").strip().lower()
    category = (listing.get("category") or "").strip().lower()
    note = (listing.get("note") or "").strip().lower()
    return retrofit_flag == "true" or "retrofit" in category or "retrofit" in note


def snapshot(listing):
    """The subset of fields worth remembering/logging for a listing."""
    return {
        "family": listing.get("family", "?"),
        "year": listing.get("year", "?"),
        "throat": listing.get("throat", "?"),
        "table": listing.get("table", "?"),
        "price": listing.get("price", "?"),
        "note": listing.get("note") or listing.get("memo") or "",
        "kind": "Retrofit" if is_retrofit(listing) else "Pre-Loved",
    }


# ---------------------------------------------------------------------------
# State tracking (status per listing id, so we can detect Listed -> Sold)
# ---------------------------------------------------------------------------

def load_state():
    if not SEEN_FILE.exists():
        return {}
    raw = json.loads(SEEN_FILE.read_text())
    if isinstance(raw, list):
        # Migrate from the old flat-list format
        return {listing_id: {"status": "unknown", "listed_at": None} for listing_id in raw}
    return raw


def save_state(state):
    SEEN_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def append_to_daily_summary(new_listings):
    """Record today's new listings so the once-a-day summary email can report them."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    entries = json.loads(DAILY_FILE.read_text()) if DAILY_FILE.exists() else []
    for listing in new_listings:
        entries.append({"date": today, **listing})
    DAILY_FILE.write_text(json.dumps(entries, indent=2))


def append_log_rows(rows):
    file_exists = LOG_FILE.exists()
    with LOG_FILE.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def make_log_row(timestamp, event, listing_id, info):
    return {
        "timestamp_utc": timestamp,
        "event": event,
        "id": listing_id,
        "kind": info.get("kind", "?"),
        "family": info.get("family", "?"),
        "year": info.get("year", "?"),
        "throat": info.get("throat", "?"),
        "table": info.get("table", "?"),
        "price": info.get("price", "?"),
        "note": info.get("note", ""),
    }


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

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    state = load_state()
    current_ids = set()
    new_active_listings = []
    log_rows = []

    for listing in listings:
        lid = listing["id"]
        current_ids.add(lid)
        status = (listing.get("status") or "").strip().lower()
        info = snapshot(listing)

        if lid not in state:
            # Brand new id we've never tracked before
            if status == "active":
                new_active_listings.append(listing)
                log_rows.append(make_log_row(now, "Listed", lid, info))
                state[lid] = {"status": "active", "listed_at": now, **info}
            else:
                state[lid] = {"status": status or "unknown", "listed_at": None, **info}
            continue

        old_status = state[lid].get("status")
        if status != old_status:
            if status == "sold" and old_status == "active":
                log_rows.append(make_log_row(now, "Sold", lid, info))
            elif status == "active" and old_status in ("sold", "unknown"):
                # Re-listed
                new_active_listings.append(listing)
                log_rows.append(make_log_row(now, "Listed", lid, info))
                state[lid]["listed_at"] = now
        state[lid]["status"] = status or old_status
        state[lid].update(info)

    # If something we had marked active has vanished from the feed entirely
    # (removed rather than flagged "sold"), treat that as sold too.
    for lid, info in state.items():
        if info.get("status") == "active" and lid not in current_ids:
            log_rows.append(make_log_row(now, "Sold (removed from feed)", lid, info))
            info["status"] = "sold"

    if log_rows:
        append_log_rows(log_rows)
        print(f"Logged {len(log_rows)} event(s) to listing_log.csv")

    if new_active_listings:
        print(f"Found {len(new_active_listings)} new active listing(s). Sending email.")
        send_digest(new_active_listings)
        append_to_daily_summary(new_active_listings)
    else:
        print("No new active listings this run.")

    save_state(state)


if __name__ == "__main__":
    main()
