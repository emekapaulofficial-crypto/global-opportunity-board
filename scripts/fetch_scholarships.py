"""
Scrapes scholarship listing pages across degree levels and saves results
directly into data/listings.json -- NO Google Sheets, NO SheetDB.

For each scholarship it:
  - classifies funding level (fully funded / partial / unclear)
  - classifies whether IELTS is required, not required, or not stated
  - flags any explicit mention of Nigeria/Africa/developing-country eligibility
  - extracts a deadline and works out if it's still OPEN or already CLOSED
  - only adds genuinely new rows (never re-adds something already saved)

Run: python scripts/fetch_scholarships.py
"""
import os
import re
import json
import time
import hashlib
import datetime
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

TODAY = datetime.date.today()
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "listings.json")

LISTING_PAGES = [
    {"url": "https://scholarshiptab.com/scholarships/undergraduate", "level": "Undergraduate"},
    {"url": "https://scholarshiptab.com/scholarships/masters", "level": "Masters"},
    {"url": "https://scholarshiptab.com/scholarships/phd", "level": "PhD / Postgraduate"},
    {"url": "https://scholarshiptab.com/scholarships/fully-funded", "level": "Mixed / Fully Funded"},
]

# RSS feeds -- these are built for automated reading and are far less
# likely to block requests than a regular webpage, so they're a more
# reliable backup if ScholarshipTab keeps returning 403 Forbidden.
RSS_FEEDS = [
    {"url": "https://deerunspost.com/", "source": "Dee Runspost", "level": "Mixed / Fully Funded"},
    {"url": "https://wemakescholars.com/blog/", "source": "WeMakeScholars", "level": "Mixed / Fully Funded"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}

FULLY_FUNDED_PHRASES = [
    "fully funded", "full scholarship", "covers tuition", "tuition, accommodation",
    "stipend", "all expenses covered", "100% scholarship",
]
PARTIAL_PHRASES = ["partial scholarship", "tuition waiver only", "partial funding"]

IELTS_NOT_REQUIRED_PHRASES = [
    "ielts not required", "no ielts", "without ielts", "ielts waived",
    "ielts is not required", "english proficiency not required",
]
IELTS_MENTION_PHRASES = ["ielts"]

NIGERIA_PHRASES = ["nigeria", "nigerian"]
AFRICA_PHRASES = ["africa", "sub-saharan", "developing countries", "developing country"]

DEADLINE_PATTERN = re.compile(
    r"(?:deadline|closes|closing date)[:\s]*"
    r"((?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def extract_deadline(text):
    match = DEADLINE_PATTERN.search(text or "")
    return match.group(1) if match else ""


def parse_deadline(deadline_str):
    if not deadline_str:
        return None
    deadline_str = deadline_str.strip()

    iso_match = re.match(r"(\d{4})-(\d{2})-(\d{2})", deadline_str)
    if iso_match:
        try:
            return datetime.date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            return None

    parts = deadline_str.lower().split()
    year = None
    month = None
    day = None
    for p in parts:
        if p.isdigit() and len(p) == 4:
            year = int(p)
        elif p.isdigit():
            day = int(p)
        elif p in MONTHS:
            month = MONTHS[p]

    if year and month:
        if day:
            try:
                return datetime.date(year, month, day)
            except ValueError:
                return None
        else:
            if month == 12:
                return datetime.date(year, 12, 31)
            return datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

    return None


def classify_application_status(deadline_str):
    parsed = parse_deadline(deadline_str)
    if parsed is None:
        return "UNKNOWN"
    return "OPEN" if parsed >= TODAY else "CLOSED"


def classify_funding(text):
    t = (text or "").lower()
    for phrase in FULLY_FUNDED_PHRASES:
        if phrase in t:
            return "FULLY FUNDED"
    for phrase in PARTIAL_PHRASES:
        if phrase in t:
            return "PARTIAL FUNDING"
    return "UNCLEAR"


def classify_ielts(text):
    t = (text or "").lower()
    for phrase in IELTS_NOT_REQUIRED_PHRASES:
        if phrase in t:
            return "NOT REQUIRED"
    for phrase in IELTS_MENTION_PHRASES:
        if phrase in t:
            return "REQUIRED"
    return "NOT STATED"


def classify_nigeria_note(text):
    t = (text or "").lower()
    for phrase in NIGERIA_PHRASES:
        if phrase in t:
            return "Nigeria mentioned directly -- good sign"
    for phrase in AFRICA_PHRASES:
        if phrase in t:
            return "Africa/developing countries mentioned -- worth checking"
    return "Not stated -- verify eligibility on listing page"


def make_id(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


CARD_SELECTOR_STRATEGIES = [
    "article",
    ".scholarship-item",
    ".post-item",
    "div.card",
    "li.scholarship",
]
MIN_PLAUSIBLE_CARDS = 3


def find_cards(soup):
    for selector in CARD_SELECTOR_STRATEGIES:
        candidates = soup.select(selector)
        with_links = [c for c in candidates if c.find("a", href=True)]
        if len(with_links) >= MIN_PLAUSIBLE_CARDS:
            return with_links, selector
    return [], "none matched"


def scrape_listing_page(url, level):
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"FAILED TO FETCH {url}: {e}")
        return rows

    soup = BeautifulSoup(r.text, "html.parser")
    cards, strategy_used = find_cards(soup)

    if not cards:
        print(f"WARNING: 0 scholarship cards found on {url} (site may be blocking automated requests).")
        return rows

    print(f"  ({len(cards)} cards found on {url} using selector: {strategy_used})")

    for card in cards:
        link_tag = card.find("a", href=True)
        if not link_tag:
            continue
        title = link_tag.get_text(strip=True)
        link = link_tag["href"]
        if link.startswith("/"):
            link = "https://scholarshiptab.com" + link
        summary = card.get_text(" ", strip=True)

        if not title or len(title) < 5:
            continue

        deadline = extract_deadline(summary)

        rows.append({
            "id": make_id(link),
            "type": "scholarship",
            "source_platform": "ScholarshipTab",
            "title": title,
            "company": "",
            "location": "",
            "salary": "",
            "level": level,
            "sponsorship_status": classify_funding(summary),
            "ielts_status": classify_ielts(summary),
            "nigeria_note": classify_nigeria_note(summary),
            "application_status": classify_application_status(deadline),
            "source_url": link,
            "date_posted": "",
            "deadline": deadline,
            "date_scraped": TODAY.isoformat(),
            "search_term": url,
            "notes": "",
        })

    return rows


def discover_feed_url(homepage_url):
    """Reads a site's homepage HTML and looks for the <link rel="alternate"
    type="application/rss+xml"> tag that every WordPress/blog site publishes
    -- this finds the REAL feed address instead of guessing common paths
    like '/feed' which don't always exist."""
    try:
        r = requests.get(homepage_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  Could not load homepage {homepage_url}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    link_tag = soup.find("link", attrs={"type": "application/rss+xml"})
    if link_tag and link_tag.get("href"):
        return link_tag["href"]

    # Common fallback paths, tried only if the tag wasn't found
    for suffix in ["feed/", "feed", "rss/", "rss", "?feed=rss2"]:
        candidate = homepage_url.rstrip("/") + "/" + suffix
        try:
            test = requests.get(candidate, headers=HEADERS, timeout=10)
            if test.status_code == 200 and "<rss" in test.text[:500].lower():
                return candidate
        except Exception:
            continue

    return None


def fetch_rss_feed(homepage_url, source_name, level):
    """RSS feeds are XML meant for automated reading, so sites are much
    less likely to block this than a regular scraped webpage."""
    rows = []

    feed_url = discover_feed_url(homepage_url)
    if not feed_url:
        print(f"  Could not find an RSS feed on {homepage_url}")
        return rows

    print(f"  Found feed for {source_name}: {feed_url}")

    try:
        r = requests.get(feed_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"FAILED TO FETCH RSS {feed_url}: {e}")
        return rows

    items = root.findall(".//item")
    print(f"  ({len(items)} items found in RSS feed: {source_name})")

    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        pubdate_el = item.find("pubDate")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        link = link_el.text.strip() if link_el is not None and link_el.text else ""
        summary = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        pub_date = pubdate_el.text.strip() if pubdate_el is not None and pubdate_el.text else ""

        if not title or not link:
            continue

        deadline = extract_deadline(summary)

        rows.append({
            "id": make_id(link),
            "type": "scholarship",
            "source_platform": source_name,
            "title": title,
            "company": "",
            "location": "",
            "salary": "",
            "level": level,
            "sponsorship_status": classify_funding(summary),
            "ielts_status": classify_ielts(summary),
            "nigeria_note": classify_nigeria_note(summary),
            "application_status": classify_application_status(deadline),
            "source_url": link,
            "date_posted": pub_date[:16] if pub_date else "",
            "deadline": deadline,
            "date_scraped": TODAY.isoformat(),
            "search_term": f"RSS: {source_name}",
            "notes": "",
        })

    return rows


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


def main():
    existing = load_existing()
    existing_ids = {row.get("id", "") for row in existing if row.get("id")}
    print(f"File already has {len(existing_ids)} listings.")

    fetched = []
    for page in LISTING_PAGES:
        rows = scrape_listing_page(page["url"], page["level"])
        print(f"{page['url']}: {len(rows)} scholarships found")
        fetched.extend(rows)
        time.sleep(2)

    for feed in RSS_FEEDS:
        rows = fetch_rss_feed(feed["url"], feed["source"], feed["level"])
        fetched.extend(rows)
        time.sleep(1)

    print(f"Total scholarships fetched: {len(fetched)}")

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
