"""
Opportunity Desk job scraper.

Sources:
- Adzuna (API key required): all countries currently exposed by the Adzuna public
  jobs API.
- Arbeitnow (no key): Europe-focused jobs with its visa_sponsorship filter.

The script rotates Adzuna country/category searches so the free API budget is
spread across the week. Existing listings are preserved and de-duplicated.
"""

import datetime
import hashlib
import json
import os
import time
from pathlib import Path

import requests

TODAY = datetime.date.today()
ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "listings.json"

ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")

ADZUNA_ENDPOINT = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
ARBEITNOW_ENDPOINT = "https://www.arbeitnow.com/api/job-board-api"

# These are the country codes currently supported by Adzuna's public jobs API.
# This is broader than the original project, which omitted the US, Spain,
# France, Russia, Brazil, India and Mexico.
COUNTRIES = [
    "gb", "us", "ca", "de", "fr", "at", "nl", "pl", "it", "es",
    "ru", "br", "in", "mx", "nz", "au", "sg", "za",
]

COUNTRY_NAMES = {
    "gb": "United Kingdom", "us": "United States", "ca": "Canada",
    "de": "Germany", "fr": "France", "at": "Austria", "nl": "Netherlands",
    "pl": "Poland", "it": "Italy", "es": "Spain", "ru": "Russia",
    "br": "Brazil", "in": "India", "mx": "Mexico", "nz": "New Zealand",
    "au": "Australia", "sg": "Singapore", "za": "South Africa",
}

# Broad occupation searches. We keep “visa sponsorship” in the query so the
# feed is focused on employers that mention sponsorship, relocation or work permits.
# A listing is still independently classified from its advert text below.
CATEGORIES = [
    "electrician visa sponsorship",
    "plumber pipefitter visa sponsorship",
    "welder fabricator visa sponsorship",
    "carpenter joiner visa sponsorship",
    "bricklayer mason visa sponsorship",
    "roofer tiler painter visa sponsorship",
    "HVAC refrigeration technician visa sponsorship",
    "construction worker general labourer visa sponsorship",
    "heavy equipment operator excavator visa sponsorship",
    "mechanic automotive diesel technician visa sponsorship",
    "boilermaker fitter machinist visa sponsorship",
    "engineer technician skilled worker visa sponsorship",
    "farm agriculture worker visa sponsorship",
    "care worker caregiver aged care visa sponsorship",
    "healthcare assistant support worker visa sponsorship",
    "registered nurse midwife visa sponsorship",
    "cleaner housekeeping janitor visa sponsorship",
    "chef cook kitchen assistant visa sponsorship",
    "hotel hospitality waiter waitress visa sponsorship",
    "warehouse logistics picker packer visa sponsorship",
    "truck driver heavy vehicle driver visa sponsorship",
    "bus driver delivery driver visa sponsorship",
    "forklift operator visa sponsorship",
    "security officer visa sponsorship",
    "IT software developer skilled worker visa sponsorship",
    "accountant finance skilled worker visa sponsorship",
]

CATEGORY_RULES = [
    ("Electrician", ("electrician", "electrical")),
    ("Plumbing / Pipefitting", ("plumber", "plumbing", "pipefitter", "pipe fitter")),
    ("Welding / Fabrication", ("welder", "welding", "fabricator", "fabrication")),
    ("Carpentry / Joinery", ("carpenter", "carpentry", "joiner", "joinery")),
    ("Masonry / Bricklaying", ("bricklayer", "bricklay", "mason", "masonry")),
    ("Roofing / Tiling / Painting", ("roofer", "roofing", "tiler", "tiling", "painter", "painting")),
    ("HVAC / Refrigeration", ("hvac", "refrigeration", "air conditioning", "air-conditioning")),
    ("Construction / General Labour", ("construction", "general labour", "general labor", "labourer", "laborer")),
    ("Heavy Equipment", ("excavator", "heavy equipment", "plant operator", "crane operator")),
    ("Mechanic / Automotive", ("mechanic", "automotive", "diesel technician", "vehicle technician")),
    ("Engineering / Technical", ("engineer", "technician", "technical")),
    ("Agriculture / Farm", ("farm", "agriculture", "agricultural", "harvest")),
    ("Care / Aged Care", ("care worker", "caregiver", "carer", "aged care", "elderly care")),
    ("Healthcare / Nursing", ("nurse", "nursing", "healthcare assistant", "health care assistant", "midwife")),
    ("Cleaning / Housekeeping", ("cleaner", "cleaning", "housekeeper", "housekeeping", "janitor")),
    ("Hospitality / Kitchen", ("chef", "cook", "kitchen", "waiter", "waitress", "hospitality", "hotel")),
    ("Warehouse / Logistics", ("warehouse", "logistics", "picker", "packer", "forklift")),
    ("Driving / Transport", ("truck driver", "heavy vehicle", "bus driver", "delivery driver", "driver")),
    ("Security", ("security officer", "security guard")),
    ("IT / Software", ("software developer", "developer", "software engineer", "IT ", "information technology")),
    ("Finance / Accounting", ("accountant", "accounting", "finance")),
]

SEARCHES_PER_RUN = 8

POSITIVE_PHRASES = [
    "visa sponsorship",
    "visa sponsorship considered",
    "sponsorship available",
    "we sponsor",
    "sponsor visa",
    "will sponsor",
    "employer sponsored visa",
    "skilled worker visa",
    "sponsorship provided",
    "relocation and visa support",
    "work permit sponsorship",
]

NEGATIVE_PHRASES = [
    "no visa sponsorship",
    "unable to accommodate visa sponsorship",
    "not able to sponsor",
    "must have existing right to work",
    "no sponsorship available",
    "without sponsorship",
]


def classify_category(title: str, description: str, search_term: str = "") -> str:
    text = f"{title} {description} {search_term}".lower()
    for category, keywords in CATEGORY_RULES:
        if any(keyword.lower() in text for keyword in keywords):
            return category
    return "Other skilled / general"


def make_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def load_existing():
    if not DATA_FILE.exists():
        return []
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"Could not read {DATA_FILE}; keeping a safe empty base: {exc}")
        return []


def save_data(rows):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    tmp.replace(DATA_FILE)


def classify_sponsorship(text: str) -> str:
    t = (text or "").lower()
    if any(p in t for p in NEGATIVE_PHRASES):
        return "NO SPONSORSHIP"
    if any(p in t for p in POSITIVE_PHRASES):
        return "SPONSORED"
    return "UNCLEAR"


def get_todays_batch():
    master = [{"country": c, "what": q} for c in COUNTRIES for q in CATEGORIES]
    # 6-hour rotation. With the current matrix this completes in about a week.
    slot = datetime.datetime.utcnow().hour // 6
    rotation_index = TODAY.timetuple().tm_yday * 4 + slot
    total_batches = max(1, (len(master) + SEARCHES_PER_RUN - 1) // SEARCHES_PER_RUN)
    batch_number = rotation_index % total_batches
    start = batch_number * SEARCHES_PER_RUN
    return master[start:start + SEARCHES_PER_RUN]


def fetch_adzuna(batch):
    rows = []
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("Adzuna skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY are not set.")
        return rows

    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "OpportunityDesk/1.0"})

    for search in batch:
        country_code = search["country"]
        params = {
            "app_id": ADZUNA_APP_ID,
            "app_key": ADZUNA_APP_KEY,
            "what": search["what"],
            "results_per_page": 20,
            "sort_by": "date",
            "content-type": "application/json",
        }
        try:
            response = session.get(
                ADZUNA_ENDPOINT.format(country=country_code),
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("results", [])
        except Exception as exc:
            print(f"Adzuna failed for {country_code}/{search['what']}: {exc}")
            continue

        for job in results:
            source_url = job.get("redirect_url", "")
            if not source_url:
                continue

            description = job.get("description", "") or ""
            search_term = search["what"]
            blob = f"{job.get('title', '')} {description}"
            company = (job.get("company") or {}).get("display_name", "")
            location = (job.get("location") or {}).get("display_name", "")
            salary_min = job.get("salary_min")
            salary_max = job.get("salary_max")
            salary = ""
            if salary_min is not None and salary_max is not None:
                try:
                    salary = f"{float(salary_min):.0f}-{float(salary_max):.0f}"
                except (TypeError, ValueError):
                    pass

            rows.append({
                "id": make_id(source_url),
                "type": "job",
                "source_platform": "Adzuna",
                "title": job.get("title", ""),
                "company": company,
                "location": location or COUNTRY_NAMES.get(country_code, country_code.upper()),
                "country": COUNTRY_NAMES.get(country_code, country_code.upper()),
                "category": classify_category(job.get("title", ""), description, search_term),
                "salary": salary,
                "level": "",
                "sponsorship_status": classify_sponsorship(blob),
                "ielts_status": "",
                "nigeria_note": "",
                "application_status": "OPEN",
                "source_url": source_url,
                "date_posted": (job.get("created") or "")[:10],
                "deadline": "",
                "date_scraped": TODAY.isoformat(),
                "search_term": f"{search['what']} ({COUNTRY_NAMES.get(country_code, country_code.upper())})",
                "notes": "",
            })
        time.sleep(0.6)

    return rows


def fetch_arbeitnow():
    rows = []
    try:
        response = requests.get(
            ARBEITNOW_ENDPOINT,
            params={"visa_sponsorship": "true"},
            headers={"Accept": "application/json", "User-Agent": "OpportunityDesk/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        jobs = response.json().get("data", [])
    except Exception as exc:
        print(f"Arbeitnow failed: {exc}")
        return rows

    for job in jobs:
        url = job.get("url", "")
        if not url:
            continue

        date_posted = ""
        created_at = job.get("created_at")
        if created_at:
            try:
                date_posted = datetime.date.fromtimestamp(int(created_at)).isoformat()
            except (TypeError, ValueError, OSError):
                pass

        location = job.get("location", "") or ""
        rows.append({
            "id": make_id(url),
            "type": "job",
            "source_platform": "Arbeitnow",
            "title": job.get("title", ""),
            "company": job.get("company_name", ""),
            "location": location,
            "country": guess_country(location),
            "category": classify_category(job.get("title", ""), job.get("description", ""), "visa sponsorship"),
            "salary": "",
            "level": "",
            "sponsorship_status": "SPONSORED",
            "ielts_status": "",
            "nigeria_note": "",
            "application_status": "OPEN",
            "source_url": url,
            "date_posted": date_posted,
            "deadline": "",
            "date_scraped": TODAY.isoformat(),
            "search_term": "visa_sponsorship=true",
            "notes": "Arbeitnow native visa-sponsorship filter.",
        })
    return rows


def guess_country(location: str) -> str:
    text = (location or "").lower()
    aliases = {
        "uk": "United Kingdom", "united kingdom": "United Kingdom", "england": "United Kingdom",
        "scotland": "United Kingdom", "wales": "United Kingdom",
        "usa": "United States", "united states": "United States",
        "canada": "Canada", "germany": "Germany", "france": "France",
        "austria": "Austria", "netherlands": "Netherlands", "poland": "Poland",
        "italy": "Italy", "spain": "Spain", "brazil": "Brazil", "india": "India",
        "mexico": "Mexico", "new zealand": "New Zealand", "australia": "Australia",
        "singapore": "Singapore", "south africa": "South Africa", "ireland": "Ireland",
        "switzerland": "Switzerland", "belgium": "Belgium", "denmark": "Denmark",
        "sweden": "Sweden", "norway": "Norway", "finland": "Finland",
    }
    for key, name in aliases.items():
        if key in text:
            return name
    return ""


def merge_rows(existing, fetched):
    by_id = {}
    for row in existing:
        if row.get("id"):
            by_id[row["id"]] = row

    for row in fetched:
        if not row.get("id"):
            continue
        if row["id"] in by_id:
            # Refresh fields that commonly change while preserving the record.
            by_id[row["id"]].update({
                k: v for k, v in row.items()
                if v not in ("", None)
            })
        else:
            by_id[row["id"]] = row

    return list(by_id.values())


def main():
    existing = load_existing()
    print(f"Existing listings: {len(existing)}")

    batch = get_todays_batch()
    print(f"Adzuna batch: {len(batch)} searches")
    for item in batch:
        print("  -", item["what"], "(", COUNTRY_NAMES.get(item["country"], item["country"]), ")")

    fetched = fetch_adzuna(batch)
    fetched.extend(fetch_arbeitnow())
    print(f"Fetched this run: {len(fetched)}")

    combined = merge_rows(existing, fetched)
    save_data(combined)
    print(f"Saved {len(combined)} total listings to {DATA_FILE}")


if __name__ == "__main__":
    main()
