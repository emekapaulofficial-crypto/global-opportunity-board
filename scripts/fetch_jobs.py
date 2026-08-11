"""
Fetches visa-sponsorship jobs from Adzuna + Arbeitnow and saves results
directly into data/listings.json inside this repository -- NO Google
Sheets, NO SheetDB, nothing external to manage.

The GitHub Actions workflow commits this updated file back to the repo
automatically after every run, and the website reads it directly.

FOCUS: this feed is built for roles most Nigerians applying through Global
Travel Agency are actually looking for -- construction, cleaning,
hospitality, care work, warehouse/logistics, driving, agriculture, and
skilled trades. Tech/IT/office jobs are deliberately excluded.

WHY ROTATION: Adzuna's free tier only allows a limited number of searches
per month. Construction and cleaning searches run on EVERY execution (so
those categories are never starved), while the remaining blue-collar
categories rotate through a larger matrix of countries over time.

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
# PRIORITY categories -- these run on EVERY execution, no rotation, so
# construction and cleaning jobs are never skipped in favour of anything
# else. Countries for these still rotate so coverage spreads over time.
#
# IMPORTANT: each entry is a set of OCCUPATION words only -- no "visa
# sponsorship" text. Adzuna's default search requires every word in the
# query to appear together, so a query like "bricklayer mason visa
# sponsorship" demands a posting literally contain all four words at once,
# which almost never happens even for genuinely sponsored jobs. Instead we
# search broadly for the occupation (using "match any of these words") and
# then check the actual job description for sponsorship language
# afterward, via classify_sponsorship() -- see fetch_adzuna() below.
# ---------------------------------------------------------------------------
PRIORITY_CATEGORIES = [
    "construction laborer labourer builder groundworker",
    "bricklayer mason",
    "scaffolder",
    "cleaner cleaning housekeeping janitor custodian",
]

# ---------------------------------------------------------------------------
# Other blue-collar categories worth checking for a Nigerian audience.
# These rotate through the country matrix over multiple runs.
# Deliberately NO tech/IT/office/professional categories in here.
# Same "occupation words only" approach as above.
# ---------------------------------------------------------------------------
OTHER_CATEGORIES = [
    "farm worker agriculture harvest picker",
    "aged care carer support worker",
    "hospitality hotel housekeeping",
    "kitchen porter chef cook catering",
    "warehouse logistics picker packer forklift",
    "driver delivery hgv lorry",
    "welder electrician plumber fitter",
    "healthcare assistant nursing assistant",
    "meat processing factory worker abattoir",
    "security guard officer",
]

COUNTRIES = ["au", "gb", "ca", "de", "nl", "at", "pl", "za", "sg", "it", "fr"]

PRIORITY_SEARCHES = [
    {"country": c, "what_or": cat} for c in COUNTRIES for cat in PRIORITY_CATEGORIES
]
ROTATING_SEARCHES = [
    {"country": c, "what_or": cat} for c in COUNTRIES for cat in OTHER_CATEGORIES
]

# Total Adzuna calls per run, split between the two pools below.
PRIORITY_PER_RUN = 4   # always construction/cleaning
ROTATING_PER_RUN = 4   # rotates through everything else


def _rotate_slice(pool, size):
    """Picks a rotating slice of `pool`, advancing based on day-of-year and
    6-hour time slot so it cycles through the whole matrix automatically
    with no manual updating."""
    if not pool:
        return []
    hour_slot = datetime.datetime.utcnow().hour // 6  # 0,1,2,3
    rotation_index = TODAY.timetuple().tm_yday * 4 + hour_slot
    total_batches = max(1, len(pool) // size)
    batch_number = rotation_index % total_batches
    start = batch_number * size
    return pool[start:start + size]


def get_todays_batch():
    priority = _rotate_slice(PRIORITY_SEARCHES, PRIORITY_PER_RUN)
    rotating = _rotate_slice(ROTATING_SEARCHES, ROTATING_PER_RUN)
    return priority + rotating


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


# ---------------------------------------------------------------------------
# Category classifier -- labels each job so the site can filter/display by
# type, and so anything that doesn't match a real blue-collar category (i.e.
# tech/office jobs slipping through Arbeitnow's general feed) gets dropped.
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "Construction": [
        "construction", "laborer", "labourer", "bricklayer", "mason",
        "scaffold", "site worker", "carpenter", "roofer", "groundworker",
        "concrete", "builder",
    ],
    "Cleaning": [
        "cleaner", "cleaning", "housekeeping", "janitor", "custodian",
        "sanitation",
    ],
    "Hospitality": [
        "hospitality", "hotel", "kitchen porter", "chef", "cook", "waiter",
        "waitress", "catering", "barista", "bartender",
    ],
    "Care Work": [
        "care worker", "aged care", "healthcare assistant", "carer",
        "support worker", "nursing assistant",
    ],
    "Warehouse & Logistics": [
        "warehouse", "logistics", "picker", "packer", "forklift",
        "distribution centre", "distribution center",
    ],
    "Driving": ["driver", "delivery driver", "hgv", "lorry", "truck driver"],
    "Agriculture": [
        "farm worker", "agriculture", "harvest", "fruit picker", "abattoir",
        "meat processing", "poultry",
    ],
    "Skilled Trades": ["welder", "electrician", "plumber", "fitter"],
    "Security": ["security guard", "security officer"],
}

# Any job whose title/description strongly matches these is almost
# certainly a tech/IT/office role -- explicitly excluded even if it
# happens to carry a "visa sponsorship" tag.
TECH_EXCLUDE_KEYWORDS = [
    "software", "developer", "engineer", "engineering", "devops", "backend",
    "frontend", "full stack", "full-stack", "data scientist", "data analyst",
    "product manager", "product owner", "ux designer", "ui designer",
    "qa engineer", "cyber security", "cybersecurity", "it support",
    "sysadmin", "cloud engineer", "machine learning", "artificial intelligence",
    "python developer", "java developer", "javascript", "react developer",
    "node.js", "sql developer", "network engineer", "it technician",
    "help desk", "scrum master", "programmer", "web designer",
]


def classify_category(text):
    t = (text or "").lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                return category
    return ""  # no match -- not one of our target categories


def is_tech_job(text):
    t = (text or "").lower()
    return any(kw in t for kw in TECH_EXCLUDE_KEYWORDS)


# ---------------------------------------------------------------------------
# Employer-type classifier -- flags likely recruitment/staffing agency
# postings vs. what looks like a direct employer, since the goal is real,
# legit jobs with sponsorship straight from the hiring company.
# ---------------------------------------------------------------------------
AGENCY_KEYWORDS = [
    "recruitment", "recruiting", "staffing", "manpower", "employment agency",
    "personnel", "talent solutions", "labour hire", "labor hire", "hr solutions",
]


def classify_employer_type(company, text):
    combined = f"{company or ''} {text or ''}".lower()
    for kw in AGENCY_KEYWORDS:
        if kw in combined:
            return "LIKELY RECRUITMENT AGENCY"
    if company and company.strip():
        return "DIRECT EMPLOYER"
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


def clean_existing_jobs(rows):
    """One-time (and ongoing) cleanup: removes job listings that were saved
    BEFORE the tech-job filter and category classifier existed. Without
    this, old tech jobs saved by the previous version of this script would
    sit in listings.json forever, since the rest of the script only ever
    ADDS new rows -- it never re-checks old ones. Scholarship rows
    (type == "scholarship") are left untouched."""
    cleaned = []
    removed = 0
    for row in rows:
        if row.get("type") != "job":
            cleaned.append(row)
            continue

        blob = f"{row.get('title', '')} {row.get('company', '')} {row.get('notes', '')}"

        if is_tech_job(blob):
            removed += 1
            continue

        category = row.get("category") or classify_category(blob)
        if not category:
            removed += 1
            continue

        # backfill the category field for older rows saved before it existed
        if not row.get("category"):
            row["category"] = category
        if not row.get("employer_type"):
            row["employer_type"] = classify_employer_type(row.get("company", ""), blob)

        cleaned.append(row)

    if removed:
        print(f"Cleanup: removed {removed} old listings that no longer match target categories (tech/unclassified jobs).")
    return cleaned


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
            "what_or": search["what_or"],
            "results_per_page": 30,
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

        kept = 0
        for job in results:
            source_url = job.get("redirect_url", "")
            if not source_url:
                continue
            title = job.get("title", "")
            description = job.get("description", "")
            blob = title + " " + description

            if is_tech_job(blob):
                continue  # explicitly excluded even if it had a sponsorship tag

            category = classify_category(blob)
            if not category:
                continue  # doesn't match construction/cleaning/etc -- skip it

            sponsorship_status = classify_sponsorship(blob)
            if sponsorship_status == "NO SPONSORSHIP":
                continue  # explicitly says no sponsorship -- not useful, skip it

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
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "category": category,
                "employer_type": classify_employer_type(company, blob),
                "level": "",
                "sponsorship_status": sponsorship_status,
                "ielts_status": "",
                "nigeria_note": "",
                "application_status": "",
                "source_url": source_url,
                "date_posted": date_posted,
                "deadline": "",
                "date_scraped": TODAY.isoformat(),
                "search_term": search["what_or"] + " (" + search["country"] + ")",
                "notes": "",
            })
            kept += 1
        print(f"  Adzuna '{search['what_or']}' ({search['country']}): {len(results)} results, {kept} kept after filtering")
        time.sleep(1)
    return rows


def fetch_arbeitnow():
    """Arbeitnow's general feed is heavily tech/IT-skewed, so results are
    filtered hard here: a job only survives if it matches one of our target
    blue-collar categories AND does not look like a tech role."""
    rows = []
    params = {"visa_sponsorship": "true"}
    try:
        r = requests.get(ARBEITNOW_ENDPOINT, params=params, timeout=20)
        r.raise_for_status()
        jobs = r.json().get("data", [])
    except Exception as e:
        print(f"Arbeitnow fetch failed: {e}")
        return rows

    kept = 0
    for job in jobs:
        url = job.get("url", "")
        if not url:
            continue
        title = job.get("title", "")
        description = job.get("description", "")
        tags = " ".join(job.get("tags", []) or [])
        blob = f"{title} {description} {tags}"

        if is_tech_job(blob):
            continue

        category = classify_category(blob)
        if not category:
            continue

        created_at = job.get("created_at")
        date_posted = ""
        if created_at:
            try:
                date_posted = datetime.date.fromtimestamp(int(created_at)).isoformat()
            except Exception:
                date_posted = ""

        company = job.get("company_name", "")

        rows.append({
            "id": make_id(url),
            "type": "job",
            "source_platform": "Arbeitnow",
            "title": title,
            "company": company,
            "location": job.get("location", ""),
            "salary": "",
            "category": category,
            "employer_type": classify_employer_type(company, blob),
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
        kept += 1
    print(f"  Arbeitnow: {len(jobs)} sponsored jobs returned, {kept} kept after removing tech/non-target roles")
    return rows


def main():
    batch = get_todays_batch()
    print(f"This run's search batch ({len(batch)} searches):")
    for s in batch:
        print("  - " + s["what_or"] + " (" + s["country"] + ")")

    existing = load_existing()
    print(f"File already has {len(existing)} listings.")
    existing = clean_existing_jobs(existing)
    existing_ids = {row.get("id", "") for row in existing if row.get("id")}
    print(f"{len(existing)} listings remain after removing outdated tech/unclassified jobs.")

    fetched = []
    fetched += fetch_adzuna(batch)
    fetched += fetch_arbeitnow()
    print(f"Fetched {len(fetched)} total listings from all platforms (after category/tech filtering).")

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
