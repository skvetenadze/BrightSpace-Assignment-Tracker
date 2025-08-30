import os
import json
import time
import requests
from datetime import datetime, timedelta
import pytz

from icalendar import Calendar
import recurring_ical_events

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import APIError
from dotenv import load_dotenv
load_dotenv()


# =========================
# Config (env-driven)
# =========================
SHEET_NAME = os.environ.get("SHEET_NAME", "Assignment Tracker")
LOCAL_TZ = pytz.timezone(os.environ.get("LOCAL_TZ", "America/New_York"))
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "14"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "1800"))  # 30 minutes

# Comma-separated list of Brightspace iCal URLs
ICS_URLS = [u.strip() for u in os.environ.get("BRIGHTSPACE_ICS_URLS", "").split(",") if u.strip()]

# Google Sheets auth (service account JSON in env)
# Google Sheets auth (service account file OR inline JSON via env)
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

GOOGLE_CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE")   # e.g., "credentials.json"
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")             # inline JSON (single line)

if GOOGLE_CREDENTIALS_FILE:
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
elif GOOGLE_CREDENTIALS:
    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(GOOGLE_CREDENTIALS), scope)
else:
    raise SystemExit(
        "Provide either GOOGLE_CREDENTIALS_FILE=<path-to-json> or GOOGLE_CREDENTIALS=<inline-JSON> in your environment."
    )


# =========================
# Helpers
# =========================
def _to_local(dt_obj):
    """
    Normalize any dt (date or datetime, tz-aware or naive) to LOCAL_TZ, return aware datetime.
    - If it's date-only, assume 23:59 local to make it visible on the due date.
    - If it's naive, assume UTC and convert.
    """
    if isinstance(dt_obj, datetime):
        if dt_obj.tzinfo is None:
            # Treat naive as UTC
            dt_obj = pytz.utc.localize(dt_obj)
    else:
        # date-only (no time): set to 23:59 local
        dt_obj = datetime(dt_obj.year, dt_obj.month, dt_obj.day, 23, 59, 0, tzinfo=pytz.utc)

    return dt_obj.astimezone(LOCAL_TZ)


def _event_course(cal, comp):
    """Try to infer course name from calendar metadata or categories."""
    calname = cal.get("X-WR-CALNAME")
    if calname:
        return str(calname)
    cats = comp.get("CATEGORIES")
    return str(cats) if cats else "Course"


def _event_uid(comp):
    """Stable UID including DTSTART stamp so recurring instances don't collide."""
    uid = str(comp.get("UID", "")).strip()
    dtstart = comp.get("DTSTART")
    stamp = ""
    if dtstart:
        dt = dtstart.dt
        if isinstance(dt, datetime):
            # Normalize to UTC for stable stamp
            if dt.tzinfo is not None:
                dt = dt.astimezone(pytz.utc)
            else:
                dt = pytz.utc.localize(dt)
            stamp = dt.strftime("%Y%m%dT%H%M%SZ")
        else:
            # date-only
            stamp = dt.isoformat()
    return f"{uid}#{stamp}" if uid else stamp


# =========================
# Core: fetch + transform
# =========================
def fetch_assignments_from_brightspace():
    if not ICS_URLS:
        raise SystemExit("Set BRIGHTSPACE_ICS_URLS to one or more Brightspace iCal URLs (comma-separated).")

    now_local = datetime.now(LOCAL_TZ)
    end_local = now_local + timedelta(days=WINDOW_DAYS)

    results = []

    for url in ICS_URLS:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            cal = Calendar.from_ical(r.content)
        except Exception as e:
            print(f"[WARN] Failed to fetch/parse ICS: {url} -> {e}")
            continue

        # Expand recurring events inside the window (use UTC bounds)
        try:
            # Convert window bounds to UTC for the library
            now_utc = now_local.astimezone(pytz.utc)
            end_utc = end_local.astimezone(pytz.utc)
            instances = recurring_ical_events.of(cal).between(now_utc, end_utc)
        except Exception as e:
            print(f"[WARN] Recurring expansion failed ({url}): {e}. Falling back to raw VEVENTs.")
            instances = [c for c in cal.walk("VEVENT")]

        for comp in instances:
            dtstart = comp.get("DTSTART")
            if not dtstart:
                continue

            start = dtstart.dt
            due_local = _to_local(start)

            # Filter window in local time
            if not (now_local <= due_local <= end_local):
                continue

            title = str(comp.get("SUMMARY", "")).strip() or "No Name"
            course = _event_course(cal, comp)
            uid = _event_uid(comp)

            formatted_due = due_local.strftime("%m/%d/%Y")

            results.append({
                "Assignment": title,
                "Subject/Course": course,
                "Status": "Not Started",
                "Due Date": formatted_due,
                "Priority Level": "Standard",
                "Due Date Raw": due_local,   # for sorting only
                "UID": uid,                  # not written by default, but handy if you later want de-dupe by UID
                "Source": url
            })

    # Sort by due date ascending
    results.sort(key=lambda x: x["Due Date Raw"])
    # Drop raw field before returning (your uploader writes columns B..G)
    for r in results:
        r.pop("Due Date Raw", None)
    return results


# =========================
# Google Sheets upload
# =========================
def upload_to_google_sheets(data):
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1

    # We treat col B as "Assignment" 
    max_retries = 5
    for attempt in range(max_retries):
        try:
            existing_assignments = sheet.col_values(2)  # column B
            break
        except APIError as e:
            print(f"Google API error: {e}. Retrying ({attempt + 1}/{max_retries})...")
            time.sleep(10)
    else:
        print("Failed to fetch existing assignments after multiple retries.")
        return

    # Only add new assignment titles not already in column B
    new_data = [item for item in data if item["Assignment"] not in existing_assignments]
    if not new_data:
        print("No new assignments to update.")
        return

    start_row = len(existing_assignments) + 1
    rows = []
    for idx, item in enumerate(new_data):
        # Columns B..G:
        # B Assignment | C Subject/Course | D Status | E Due Date | F Days Left (formula) | G Priority Level
        rows.append([
            item["Assignment"],
            item["Subject/Course"],
            item["Status"],
            item["Due Date"],
            f"=E{start_row + idx}-TODAY()",
            item["Priority Level"],
        ])

    end_row = start_row + len(rows) - 1
    cell_range = f"B{start_row}:G{end_row}"

    try:
        sheet.update(cell_range, rows, value_input_option="USER_ENTERED")
        print(f"Added {len(new_data)} new assignments to Google Sheet: {SHEET_NAME}, starting from row {start_row}")
    except APIError as e:
        print(f"Failed to update Google Sheets: {e}")


# =========================
# Main loop (30 min)
# =========================
if __name__ == "__main__":
    while True:
        print("Checking Brightspace calendars for new assignments...")
        try:
            assignments = fetch_assignments_from_brightspace()
            if assignments:
                upload_to_google_sheets(assignments)
            else:
                print("No assignments found in the next window.")
        except Exception as e:
            print(f"[ERROR] {e}")
        print(f"Waiting {POLL_SECONDS} seconds before the next check...")
        time.sleep(POLL_SECONDS)
