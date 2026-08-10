"""
Fetches jobs from Adzuna + Arbeitnow + Jobicy, classifies visa-sponsorship
status, and appends ONLY NEW listings (never seen before) into a local
JSON file (data/listings.json) that the website reads directly. No
Google Sheet / SheetDB involved.

Env vars required (set as GitHub Actions secrets):
  ADZUNA_APP_ID
  ADZUNA_APP_KEY

Run: python scripts/fetch_jobs.py
"""
import os
import json
import time
import hashlib
import datetime
import requests

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")

# Path to the JSON file that IS the database. The site's index.html fetches
# this file directly. Path is relative to the repo root (the workflow runs
# scripts from the repo root).
DATA_FILE = os.path.join("data", "listings.json")

ADZUNA_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
ARBEITNOW_ENDPOINT = "https://www.arbeitnow.com/api/job-board-api"
JOBICY_ENDPOINT = "https://jobicy.com/api/v2/remote-jobs"

TODAY = datetime.date.today().isoformat()

# ---------------------------------------------------------------------------
# Full search matrix: every country x category combo worth checking for
# Nigerian skilled/manual worker sponsorship. This list can be as long as
# you want -- it does NOT all run in a single execution. Each run only
# takes a rotating SLICE of it (see pick_todays_slice below), so the free
# Adzuna quota is respected while the FULL matrix still gets covered over
# a few days automatically, with no manual updating required.
#
# Adzuna country codes: gb, us, de, fr, nl, ca, au, ie, nz, se, no, dk, at, pl, za
# ---------------------------------------------------------------------------
ADZUNA_COUNTRIES = ["au", "gb", "ca", "de", "nl", "ie", "nz", "se", "at", "pl", "za"]
ADZUNA_CATEGORIES = [
    "construction visa sponsorship",
    "skilled trade visa sponsorship",
    "care worker visa sponsorship",
    "aged care worker visa sponsorship",
    "agriculture farm worker visa sponsorship",
    "welder visa sponsorship",
    "warehouse logistics visa sponsorship",
    "hospitality visa sponsorship",
    "healthcare assistant visa sponsorship",
    "nursing visa sponsorship",
]
FULL_MATRIX = [
    {"country": c, "what": cat} for c in ADZUNA_COUNTRIES for cat in ADZUNA_CATEGORIES
]

# How many combos to actually query per run. Keep this in line with your
# Adzuna free-tier daily/monthly quota and how many times a day the GitHub
# Actions workflow runs. 8 combos x 4 runs/day = 32 calls/day, well inside
# a 1000/month free allowance.
COMBOS_PER_RUN = 8


def pick_todays_slice(matrix: list, size: int) -> list:
    """Rotate through the full matrix automatically based on the day of
    year, so every combo gets covered on a cycle without any manual
    editing. No two consecutive days query the exact same slice."""
    if not matrix:
        return []
    day_index = datetime.date.today().timetuple().tm_yday
    start = (day_index * size) % len(matrix)
    # wrap around the end of the list back to the start
    return [matrix[(start + i) % len(matrix)] for i in range(size)]


ADZUNA_SEARCHES = pick_todays_slice(FULL_MATRIX, COMBOS_PER_RUN)

# ---------------------------------------------------------------------------
# Visa sponsorship keyword classifier (Adzuna results aren't structurally
# tagged, so we scan the text). Arbeitnow is tagged natively by the platform.
# ---------------------------------------------------------------------------
POSITIVE_PHRASES = [
    "visa sponsorship", "visa sponsorship considered", "sponsorship available",
    "we sponsor", "sponsor visa", "will sponsor", "employer sponsored visa",
    "skilled worker visa", "sponsorship provided", "relocation and visa support",
]
NEGATIVE_PHRASES = [
    "no visa sponsorship", "unable to accommodate visa sponsorship",
    "not able to sponsor", "must have existing right to work",
    "no sponsorship available", "without sponsorship",
]


def classify_sponsorship(text: str) -> str:
    t = (text or "").lower()
    for phrase in NEGATIVE_PHRASES:
        if phrase in t:
            return "NO SPONSORSHIP"
    for phrase in POSITIVE_PHRASES:
        if phrase in t:
            return "SPONSORED"
    return "UNCLEAR"


def make_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Duplicate prevention -- read what's already in the local JSON file BEFORE
# fetching, so we never re-add a listing that's already there.
# ---------------------------------------------------------------------------
def load_existing_rows() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            rows = json.load(f)
        return rows if isinstance(rows, list) else []
    except Exception as e:
        print(f"Could not read {DATA_FILE} (starting fresh): {e}")
        return []


# ---------------------------------------------------------------------------
# Platform fetchers -- each returns a list of normalized row dicts
# ---------------------------------------------------------------------------
def fetch_adzuna():
    rows = []
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("Skipping Adzuna (no ADZUNA_APP_ID/ADZUNA_APP_KEY set)")
        return rows

    for search in ADZUNA_SEARCHES:
        url = ADZUNA_ENDPOINT.format(country=search["country"])
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": search["what"],
            "results_per_page": 20,
            "sort_by": "date",  # newest first
            "content-type": "application/json",
        }
        try:
            r = requests.get(url, params=params, timeout=20)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            print(f"Adzuna search failed for {search}: {e}")
            continue

        for job in results:
            source_url = job.get("redirect_url", "")
            if not source_url:
                continue
            blob = f"{job.get('title','')} {job.get('description','')}"
            company =
