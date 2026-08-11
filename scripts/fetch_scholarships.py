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

SCHOLARSHIP_API_KEY = os.environ.get("SCHOLARSHIP_API_KEY", "")
SCHOLARSHIP_API_ENDPOINT = "https://api.scholarshipapi.com/v1/search"

# Queries to run against ScholarshipAPI each time. Kept modest -- free tier
# allows 100 requests/day, script runs 4x/day, so this stays well inside
# that limit even with room to spare.
SCHOLARSHIP_API_QUERIES = [
    {"q": "international students", "level": "Mixed / Fully Funded"},
    {"q": "undergraduate", "level": "Undergraduate"},
    {"q": "masters", "level": "Masters"},
    {"q": "phd", "level": "PhD / Postgraduate"},
    {"q": "fully funded", "level": "Mixed / Fully Funded"},
]

LISTING_PAGES = [
    {"url": "https://scholarshiptab.com/scholarships/undergraduate", "level": "Undergraduate"},
    {"url": "https://scholarshiptab.com/scholarships/masters", "level": "Masters"},
    {"url": "https://scholarshiptab.com/scholarships/phd", "level": "PhD / Postgraduate"},
    {"url": "https://scholarshiptab.com/scholarships/fully-funded", "level": "Mixed / Fully Funded"},
]

# RSS feeds -- these are built for automated reading and are far less
# likely to block requests than a regular webpage, so they're a more
# reliable backup if ScholarshipTab keeps returning 403 Forbidden.
# NOTE: feed_url is discovered from the homepage (may resolve to RSS or Atom).
RSS_FEEDS = [
    {"url": "https://deerunspost.com/", "source": "Dee Runspost", "level": "Mixed / Fully Funded"},
    {"url": "https://wemakescholars.com/blog/", "source": "WeMakeScholars", "level": "Mixed / Fully Funded"},
]

# Confirmed, exact feed addresses (not guessed) -- mostly government portals,
# which rarely run anti-bot protection the way commercial scholarship sites do.
# NOTE: government portals frequently publish Atom, not RSS 2.0 -- EduCanada
# is Atom, which is why it was returning 0 results before this fix.
DIRECT_RSS_FEEDS = [
    {
        "url": "https://www.educanada.ca/scholarships-bourses/rss/news-nouvelles_eng.xml",
        "source": "EduCanada (Global Affairs Canada)",
        "level": "Mixed / Fully Funded",
    },
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
    """Reads a site's homepage HTML and looks for the <link rel="alternate">
    tag that every WordPress/blog site publishes -- this finds the REAL feed
    address instead of guessing common paths like '/feed' which don't always
    exist. Matches both RSS ("application/rss+xml") and Atom
    ("application/atom+xml") link types, since either is valid."""
    try:
        r = requests.get(homepage_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  Could not load homepage {homepage_url}: {e}")
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    for feed_type in ("application/rss+xml", "application/atom+xml"):
        link_tag = soup.find("link", attrs={"type": feed_type})
        if link_tag and link_tag.get("href"):
            return link_tag["href"]

    # Common fallback paths, tried only if no <link> tag was found
    for suffix in ["feed/", "feed", "rss/", "rss", "atom/", "atom.xml", "?feed=rss2"]:
        candidate = homepage_url.rstrip("/") + "/" + suffix
        try:
            test = requests.get(candidate, headers=HEADERS, timeout=10)
            head = test.text[:500].lower()
            if test.status_code == 200 and ("<rss" in head or "<feed" in head):
                return candidate
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# Feed parsing -- handles BOTH RSS 2.0 and Atom.
#
# Government/institutional feeds (like EduCanada) very often publish Atom,
# not RSS 2.0. Atom uses different tag names (<entry> instead of <item>,
# <link href="..."/> instead of <link>text</link>, <summary>/<content>
# instead of <description>, <updated> instead of <pubDate>) and everything
# lives under the "http://www.w3.org/2005/Atom" namespace. Matching on the
# element's *local* name (ignoring namespace) lets one code path handle both
# formats without needing to special-case every feed.
# ---------------------------------------------------------------------------

def _local_name(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_entries(root):
    """Returns (entries, format) -- looks for RSS <item> first, then Atom
    <entry>, searching at any depth/namespace."""
    items = [el for el in root.iter() if _local_name(el.tag) == "item"]
    if items:
        return items, "rss"
    entries = [el for el in root.iter() if _local_name(el.tag) == "entry"]
    if entries:
        return entries, "atom"
    return [], "unknown"


def _child_text(entry, name):
    for child in entry:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _entry_link(entry):
    """RSS: <link>https://...</link> (value is the text content).
    Atom: <link href="https://..." rel="alternate"/> (value is an attribute,
    and there can be several <link> elements with different rel values)."""
    links = [child for child in entry if _local_name(child.tag) == "link"]
    if not links:
        return ""
    # Prefer an Atom "alternate" link (the human-readable page)
    for l in links:
        href = l.get("href")
        if href and l.get("rel", "alternate") == "alternate":
            return href
    # Fall back to any href, then to RSS-style text content
    for l in links:
        if l.get("href"):
            return l.get("href")
    return (links[0].text or "").strip()


def _clean_summary(text):
    """Atom <summary>/<content> can contain embedded HTML markup; strip it
    down to plain text so keyword classification works correctly."""
    if not text:
        return ""
    if "<" in text and ">" in text:
        return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return text.strip()


def parse_feed(xml_bytes, source_name):
    """Parses raw feed XML bytes and returns a normalized list of
    (title, link, summary, pub_date) tuples, regardless of RSS vs Atom."""
    root = ET.fromstring(xml_bytes)
    entries, feed_format = _find_entries(root)

    if feed_format == "unknown":
        print(f"  WARNING: {source_name} feed has neither <item> nor <entry> elements -- "
              f"unrecognized feed format, 0 results.")
        return []

    print(f"  ({len(entries)} entries found in {feed_format.upper()} feed: {source_name})")

    parsed = []
    for entry in entries:
        title = _child_text(entry, "title")
        link = _entry_link(entry)
        if not title or not link:
            continue

        if feed_format == "atom":
            summary = _child_text(entry, "summary") or _child_text(entry, "content")
            pub_date = _child_text(entry, "updated") or _child_text(entry, "published")
        else:
            summary = _child_text(entry, "description")
            pub_date = _child_text(entry, "pubDate")

        parsed.append((title, link, _clean_summary(summary), pub_date))

    return parsed


def _rows_from_feed(xml_bytes, source_name, level, search_term):
    rows = []
    for title, link, summary, pub_date in parse_feed(xml_bytes, source_name):
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
            "search_term": search_term,
            "notes": "",
        })
    return rows


def fetch_rss_feed(homepage_url, source_name, level):
    """Feeds (RSS or Atom) are XML meant for automated reading, so sites are
    much less likely to block this than a regular scraped webpage."""
    feed_url = discover_feed_url(homepage_url)
    if not feed_url:
        print(f"  Could not find a feed on {homepage_url}")
        return []

    print(f"  Found feed for {source_name}: {feed_url}")

    try:
        r = requests.get(feed_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"FAILED TO FETCH FEED {feed_url}: {e}")
        return []

    try:
        return _rows_from_feed(r.content, source_name, level, f"RSS: {source_name}")
    except ET.ParseError as e:
        print(f"FAILED TO PARSE FEED {feed_url}: {e}")
        return []


def fetch_direct_rss(feed_url, source_name, level):
    """For confirmed, exact feed addresses -- skips the discovery step
    entirely since we already know the real feed URL. Handles RSS and Atom
    equally (see parse_feed above)."""
    try:
        r = requests.get(feed_url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"FAILED TO FETCH RSS {feed_url}: {e}")
        return []

    try:
        return _rows_from_feed(r.content, source_name, level, f"RSS: {source_name}")
    except ET.ParseError as e:
        print(f"FAILED TO PARSE FEED {feed_url}: {e}")
        return []


def fetch_scholarship_api():
    """Real REST API, no scraping/blocking risk at all. Currently covers
    Australia and New Zealand universities (their coverage is expanding).
    Requires a free API key from scholarshipapi.com."""
    rows = []
    if not SCHOLARSHIP_API_KEY:
        print("Skipping ScholarshipAPI (no SCHOLARSHIP_API_KEY set)")
        return rows

    headers = {
        "Authorization": f"Bearer {SCHOLARSHIP_API_KEY}",
        "Content-Type": "application/json",
    }

    for query in SCHOLARSHIP_API_QUERIES:
        try:
            r = requests.post(
                SCHOLARSHIP_API_ENDPOINT,
                headers=headers,
                json={"q": query["q"], "limit": 20},
                timeout=20,
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
        except Exception as e:
            print(f"ScholarshipAPI query failed for '{query['q']}': {e}")
            continue

        print(f"  ScholarshipAPI '{query['q']}': {len(hits)} results")

        for hit in hits:
            name = hit.get("name", "")
            university = hit.get("university", "")
            amount = hit.get("amount")
            currency = hit.get("currency", "")
            status = (hit.get("status") or "").upper()
            close_date_ms = hit.get("closeDate")
            # This API doesn't return a direct application URL in its free
            # tier response -- fall back to a search link on their site so
            # the "View original listing" button still goes somewhere useful.
            source_url = hit.get("url") or hit.get("applicationUrl") or \
                f"https://scholarshipapi.com/scholarships?q={name.replace(' ', '+')}"

            deadline = ""
            if close_date_ms:
                try:
                    deadline = datetime.date.fromtimestamp(int(close_date_ms) / 1000).isoformat()
                except Exception:
                    deadline = ""

            salary_display = f"{amount} {currency}" if amount and currency else ""

            if not name:
                continue

            rows.append({
                "id": make_id(source_url + name),
                "type": "scholarship",
                "source_platform": "ScholarshipAPI",
                "title": name,
                "company": university,
                "location": "",
                "salary": salary_display,
                "level": query["level"],
                "sponsorship_status": "UNCLEAR",  # amount shown, but full-vs-partial not stated by free tier
                "ielts_status": "NOT STATED",
                "nigeria_note": "Not stated -- verify eligibility on listing page",
                "application_status": status if status in ("OPEN", "CLOSED") else "UNKNOWN",
                "source_url": source_url,
                "date_posted": "",
                "deadline": deadline,
                "date_scraped": TODAY.isoformat(),
                "search_term": f"ScholarshipAPI: {query['q']}",
                "notes": "",
            })
        time.sleep(1)

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

    for feed in DIRECT_RSS_FEEDS:
        rows = fetch_direct_rss(feed["url"], feed["source"], feed["level"])
        fetched.extend(rows)
        time.sleep(1)

    fetched.extend(fetch_scholarship_api())

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
