"""
Scrapes scholarship listing pages across degree levels, and for each one:
  - classifies funding level (fully funded / partial / unclear)
  - classifies whether IELTS is required, not required, or not stated
  - flags any explicit mention of Nigeria/Africa/developing-country eligibility
  - extracts a deadline and works out if it's still OPEN or already CLOSED
  - adds ONLY genuinely new rows (never re-adds something already saved)
    into a local JSON file (data/listings.json) that the website reads
    directly. No Google Sheet / SheetDB involved.

Run: python scripts/fetch_scholarships.py
"""
import os
import re
import json
import time
import hashlib
import datetime
import requests
from bs4 import BeautifulSoup

TODAY = datetime.date.today()

# Same JSON file fetch_jobs.py writes to -- this IS the database, and the
# site's index.html fetches it directly.
D
