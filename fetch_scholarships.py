"""
Opportunity Desk scholarship scraper.

ScholarshipTab changed its category URLs, so this scraper uses the current
public category pages and pagination:
  /undergraduate
  /masters
  /phd
and their fully-funded sections.

The parser looks for listing headings whose parent text contains the site's
Published / Study in / Deadline metadata. It is deliberately tolerant of
minor HTML layout changes. Existing rows are preserved and de-duplicated.
"""

import datetime
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

TODAY = datetime.date.today()
ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "listings.json"
BASE = "https://www.scholarshiptab.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Current ScholarshipTab category URLs.
CATEGORY_SOURCES = [
    ("undergraduate", "Undergraduate"),
    ("masters", "Masters"),
    ("phd", "PhD / Postgraduate"),
]

# Fully-funded pages are useful because they expose some listings that are not
# near the top of the general category pages. Duplicates are removed by URL.
FULLY_FUNDED_SOURCES = [
    ("undergraduate/fully-funded", "Undergraduate"),
    ("masters/fully-funded", "Masters"),
    ("phd/fully-funded", "PhD / Postgraduate"),
]

MAX_PAGES_PER_SOURCE = 5

FULLY_FUNDED_PHRASES = [
    "fully funded",
    "full scholarship",
    "covers tuition",
    "tuition, accommodation",
    "all expenses covered",
    "100% scholarship",
    "full tuition and stipend",
]
PARTIAL_PHRASES = [
    "partial scholarship",
    "tuition waiver only",
    "partial funding",
]

IELTS_NOT_REQUIRED_PHRASES = [
    "ielts not required",
    "no ielts",
    "without ielts",
    "ielts waived",
    "ielts is not required",
    "english proficiency not required",
]
IELTS_MENTION_PHRASES = ["ielts"]

NIGERIA_PHRASES = ["nigeria", "nigerian"]
AFRICA_PHRASES = ["africa", "sub-saharan", "developing countries", "developing country"]

DEADLINE_RE = re.compile(
    r"Deadline:\s*"
    r"((?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

PUBLISHED_RE = re.compile(
    r"Published:\s*"
    r"((?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}"
    r"|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


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
        print(f"Could not read existing listings: {exc}")
        return []


def save_data(rows):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    tmp.replace(DATA_FILE)


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass

    for fmt in ("%d %B %Y", "%B %Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    # A month-only deadline is interpreted as the last day of that month.
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", value)
    if match:
        month = datetime.datetime.strptime(match.group(1), "%B").month
        year = int(match.group(2))
        if month == 12:
            return datetime.date(year, 12, 31)
        return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

    return None


def extract_deadline(text):
    match = DEADLINE_RE.search(text or "")
    return match.group(1).strip() if match else ""


def extract_published(text):
    match = PUBLISHED_RE.search(text or "")
    return match.group(1).strip() if match else ""


def classify_application_status(deadline):
    parsed = parse_date(deadline)
    if parsed is None:
        return "UNKNOWN"
    return "OPEN" if parsed >= TODAY else "CLOSED"


def classify_funding(text):
    t = (text or "").lower()
    if any(p in t for p in FULLY_FUNDED_PHRASES):
        return "FULLY FUNDED"
    if any(p in t for p in PARTIAL_PHRASES):
        return "PARTIAL FUNDING"
    return "UNCLEAR"


def classify_ielts(text):
    t = (text or "").lower()
    if any(p in t for p in IELTS_NOT_REQUIRED_PHRASES):
        return "NOT REQUIRED"
    if any(p in t for p in IELTS_MENTION_PHRASES):
        return "REQUIRED"
    return "NOT STATED"


def classify_nigeria_note(text):
    t = (text or "").lower()
    if any(p in t for p in NIGERIA_PHRASES):
        return "Nigeria mentioned directly -- check the official eligibility"
    if any(p in t for p in AFRICA_PHRASES):
        return "Africa/developing countries mentioned -- check eligibility"
    return "Not stated -- verify eligibility on the official listing"


def extract_study_in(text):
    if not text:
        return ""
    match = re.search(
        r"Study in:\s*(.*?)(?=\s+(?:Value|Deadline):|$)",
        text,
        re.IGNORECASE,
    )
    return match.group(1).strip(" ;,") if match else ""


def find_listing_container(anchor):
    """
    Walk up a few parents until we find the listing metadata. This avoids
    relying on brittle classes such as .scholarship-item.
    """
    node = anchor
    for _ in range(6):
        node = node.parent
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        if "Published:" in text and ("Study in:" in text or "Deadline:" in text):
            return node
    return anchor.parent


def extract_cards(soup):
    seen = set()
    cards = []

    # Scholarship listing titles are headings with links on the current site.
    for anchor in soup.select("h2 a[href], h3 a[href]"):
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href", "")
        if not title or len(title) < 5 or not href:
            continue

        url = urljoin(BASE, href)
        container = find_listing_container(anchor)
        text = container.get_text(" ", strip=True)

        if "Published:" not in text and "Study in:" not in text:
            continue

        key = url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        cards.append((anchor, container, key, title, text))

    return cards


def scrape_page(session, url, level):
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        print(f"FAILED {url}: {exc}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    cards = extract_cards(soup)

    if not cards:
        print(f"WARNING: 0 scholarship cards found at {url}")
        return []

    rows = []
    for _, _, link, title, summary in cards:
        deadline = extract_deadline(summary)
        published = extract_published(summary)
        study_in = extract_study_in(summary)

        rows.append({
            "id": make_id(link),
            "type": "scholarship",
            "source_platform": "ScholarshipTab",
            "title": title,
            "company": "",
            "location": study_in,
            "country": study_in,
            "salary": "",
            "level": level,
            "sponsorship_status": classify_funding(summary),
            "ielts_status": classify_ielts(summary),
            "nigeria_note": classify_nigeria_note(summary),
            "application_status": classify_application_status(deadline),
            "source_url": link,
            "date_posted": published,
            "deadline": deadline,
            "date_scraped": TODAY.isoformat(),
            "search_term": url,
            "notes": "",
        })

    print(f"{url}: {len(rows)} scholarships found")
    return rows


def page_url(base_path, page):
    if page == 1:
        return f"{BASE}/{base_path}"
    return f"{BASE}/{base_path}/{page}"


def merge_rows(existing, fetched):
    by_id = {r.get("id"): r for r in existing if r.get("id")}

    for row in fetched:
        rid = row.get("id")
        if not rid:
            continue
        if rid in by_id:
            by_id[rid].update({k: v for k, v in row.items() if v not in ("", None)})
        else:
            by_id[rid] = row

    return list(by_id.values())


def main():
    existing = load_existing()
    session = requests.Session()
    session.headers.update(HEADERS)

    fetched = []
    sources = CATEGORY_SOURCES + FULLY_FUNDED_SOURCES

    for base_path, level in sources:
        for page in range(1, MAX_PAGES_PER_SOURCE + 1):
            url = page_url(base_path, page)
            rows = scrape_page(session, url, level)
            fetched.extend(rows)

            # If a page returns no cards, later pages usually do not exist.
            if not rows:
                break
            time.sleep(1)

    print(f"Total scholarship rows fetched this run: {len(fetched)}")

    combined = merge_rows(existing, fetched)
    save_data(combined)
    print(f"Saved {len(combined)} total listings to {DATA_FILE}")


if __name__ == "__main__":
    main()
