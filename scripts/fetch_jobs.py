"""
Fetches visa-sponsorship jobs from Adzuna + Arbeitnow and saves results
directly into data/listings.json inside this repository -- NO Google
Sheets, NO SheetDB, nothing external to manage.

The GitHub Actions workflow commits this updated file back to the repo
automatically after every run, and the website reads it directly.

WHY ROTATION: Adzuna's free tier only allows a limited number of searches
per month. To still cover many countries and job categories over time
instead of the same handful forever, this script keeps one big master
list of every country+category combination, and each run only searches a
SLICE of it, rotating to the next slice next time.

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

ADZUNA_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
ARBEITNOW_ENDPOINT = "https://www.arbeitnow.com/api/job-board-api"

TODAY = datetime.date.today()
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "listings.json")

# ---------------------------------------------------------------------------
# MASTER search list: every country x category combination worth checking
# for a Nigerian audience. Rotation (below) spreads this out automatically
# over multiple runs so the free API budget isn't blown in one go.
# Adzuna country codes used: au, gb, ca, de, nl, ie, nz, se, at, pl, za
# ---------------------------------------------------------------------------
CATEGORIES = [
    "skilled worker visa sponsorship",
    "construction visa sponsorship",
    "agriculture farm worker visa sponsorship",
    "aged care worker visa sponsorship",
    "care worker visa sponsorship",
    "hospitality visa sponsorship",
    "warehouse logistics visa sponsorship",
    "driver visa sponsorship",
    "welder electrician plumber visa sponsorship",
    "cleaner visa sponsorship",
    "healthcare assistant visa sponsorship",
    "nursing visa sponsorship",
]
COUNTRIES = ["au", "gb", "ca", "de", "nl", "ie", "nz", "se", "at", "pl", "za"]

MASTER_SEARCHES = [
    {"country": c, "what": cat} for c in COUNTRIES for cat in CATEGORIES
]

SEARCHES_PER_RUN = 8  # keeps well inside Adzuna's free monthly quota


def get_todays_batch():
    """Rotate which slice of MASTER_SEARCHES runs today, based on day of
    year and 6-hour time slot, so the whole matrix cycles through
    automatically over about a week with no manual updating."""
    hour_slot = datetime.datetime.utcnow().hour // 6  # 0,1,2,3
    rotation_index = TODAY.timetuple().tm_yday * 4 + hour_slot
    total_batches = max(1, len(MASTER_SEARCHES) // SEARCHES_PER_RUN)
    batch_number = rotation_index % total_batches
    start = batch_number * SEARCHES_PER_RUN
    return MASTER_SEARCHES[start:start + SEARCHES_PER_RUN]


# ---------------------------------------------------------------------------
# Visa sponsorship keyword classifier
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


def classify_sponsorship(text):
    t = (text or "").lower()
    for phrase in NEGATIVE_PHRASES:
        if phrase in t:
            return "NO SPONSORSHIP"
    for phrase in POSITIVE_PHRASES:
        if phrase in t:
            return "SPONSORED"
    return "UNCLEAR"


def make_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Local JSON file storage (replaces Google Sheets / SheetDB entirely)
# ---------------------------------------------------------------------------
def load_existing():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read existing data file, starting fresh: {e}")
        return []


def save_data(rows):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Platform fetchers
# ---------------------------------------------------------------------------
def fetch_adzuna(batch):
    rows = []
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("Skipping Adzuna (no ADZUNA_APP_ID/ADZUNA_APP_KEY set)")
        return rows

    for search in batch:
        url = ADZUNA_ENDPOINT.format(country=search["country"])
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": search["what"],
            "results_per_page": 20,
            "sort_by": "date",
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
            blob = job.get("title", "") + " " + job.get("description", "")
            company_obj = job.get("company") or {}
            company = company_obj.get("display_name", "")
            location_obj = job.get("location") or {}
            location = location_obj.get("display_name", "")
            salary_min = job.get("salary_min")
            salary_max = job.get("salary_max")
            if salary_min and salary_max:
                salary = f"{salary_min:.0f}-{salary_max:.0f}"
            else:
                salary = ""
            date_posted = (job.get("created") or "")[:10]

            rows.append({
                "id": make_id(source_url),
                "type": "job",
                "source_platform": "Adzuna",
                "title": job.get("title", ""),
                "company": company,
                "location": location,
                "salary": salary,
                "level": "",
                "sponsorship_status": classify_sponsorship(blob),
                "ielts_status": "",
                "nigeria_note": "",
                "application_status": "",
                "source_url": source_url,
                "date_posted": date_posted,
                "deadline": "",
                "date_scraped": TODAY.isoformat(),
                "search_term": search["what"] + " (" + search["country"] + ")",
                "notes": "",
            })
        time.sleep(1)
    return rows


def fetch_arbeitnow():
    rows = []
    params = {"visa_sponsorship": "true"}
    try:
        r = requests.get(ARBEITNOW_ENDPOINT, params=params, timeout=20)
        r.raise_for_status()
        jobs = r.json().get("data", [])
    except Exception as e:
        print(f"Arbeitnow fetch failed: {e}")
        return rows

    for job in jobs:
        url = job.get("url", "")
        if not url:
            continue
        created_at = job.get("created_at")
        date_posted = ""
        if created_at:
            try:
                date_posted = datetime.date.fromtimestamp(int(created_at)).isoformat()
            except Exception:
                date_posted = ""

        rows.append({
            "id": make_id(url),
            "type": "job",
            "source_platform": "Arbeitnow",
            "title": job.get("title", ""),
            "company": job.get("company_name", ""),
            "location": job.get("location", ""),
            "salary": "",
            "level": "",
            "sponsorship_status": "SPONSORED",
            "ielts_status": "",
            "nigeria_note": "",
            "application_status": "",
            "source_url": url,
            "date_posted": date_posted,
            "deadline": "",
            "date_scraped": TODAY.isoformat(),
            "search_term": "visa_sponsorship=true (native filter)",
            "notes": "Verified by Arbeitnow's own visa filter, not keyword-matched.",
        })
    return rows


def main():
    batch = get_todays_batch()
    print(f"This run's search batch ({len(batch)} searches):")
    for s in batch:
        print("  - " + s["what"] + " (" + s["country"] + ")")

    existing = load_existing()
    existing_ids = {row.get("id", "") for row in existing if row.get("id")}
    print(f"File already has {len(existing_ids)} listings.")

    fetched = []
    fetched += fetch_adzuna(batch)
    fetched += fetch_arbeitnow()
    print(f"Fetched {len(fetched)} total listings from all platforms.")

    new_rows = []
    seen_this_run = set()
    for row in fetched:
        if row["id"] in existing_ids or row["id"] in seen_this_run:
            continue
        seen_this_run.add(row["id"])
        new_rows.append(row)

    print(f"{len(new_rows)} of those are genuinely new.")

    combined = existing + new_rows
    save_data(combined)
    print(f"Saved {len(combined)} total listings to {DATA_FILE}")


if __name__ == "__main__":
    main()
