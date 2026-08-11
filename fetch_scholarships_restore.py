import datetime, hashlib, json, os, re, time
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

TODAY = datetime.date.today()
BASE = "https://www.scholarshiptab.com"
DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "listings.json")

LISTING_PAGES = [
    ("https://www.scholarshiptab.com/undergraduate", "Undergraduate"),
    ("https://www.scholarshiptab.com/masters", "Masters"),
    ("https://www.scholarshiptab.com/phd", "PhD / Postgraduate"),
    ("https://www.scholarshiptab.com/undergraduate/fully-funded", "Undergraduate"),
    ("https://www.scholarshiptab.com/masters/fully-funded", "Masters"),
    ("https://www.scholarshiptab.com/phd/fully-funded", "PhD / Postgraduate"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

FULLY_FUNDED = ["fully funded","full scholarship","all expenses covered","100% scholarship","covers tuition","tuition and accommodation","tuition, accommodation","tuition fee and stipend"]
PARTIAL = ["partial scholarship","partial funding","tuition waiver","fee reduction","tuition discount"]
NO_IELTS = ["ielts not required","no ielts","without ielts","ielts is not required","english proficiency not required"]
MONTHS = {m.lower(): i for i,m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"],1)}

def make_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:12]

def deadline(text):
    p = re.compile(r"(?:deadline|application deadline|closes|closing date)[:\s-]*((?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}|\d{4}-\d{2}-\d{2})", re.I)
    m = p.search(text or "")
    return m.group(1).strip() if m else ""

def parse_deadline(v):
    if not v: return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v.strip())
    if m:
        try: return datetime.date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
        except ValueError: return None
    parts = v.lower().replace(",","").split()
    year = next((int(x) for x in parts if x.isdigit() and len(x)==4), None)
    day = next((int(x) for x in parts if x.isdigit() and len(x)<=2), None)
    month = next((MONTHS[x] for x in parts if x in MONTHS), None)
    if year and month:
        if day:
            try: return datetime.date(year,month,day)
            except ValueError: return None
        return datetime.date(year,month,1) if month==12 else datetime.date(year,month+1,1)-datetime.timedelta(days=1)
    return None

def status(d):
    x=parse_deadline(d)
    return "UNKNOWN" if x is None else ("OPEN" if x >= TODAY else "CLOSED")

def funding(text):
    t=(text or "").lower()
    if any(x in t for x in FULLY_FUNDED): return "FULLY FUNDED"
    if any(x in t for x in PARTIAL): return "PARTIAL FUNDING"
    return "UNCLEAR"

def ielts(text):
    t=(text or "").lower()
    if any(x in t for x in NO_IELTS): return "NOT REQUIRED"
    return "REQUIRED" if "ielts" in t else "NOT STATED"

def eligibility(text):
    t=(text or "").lower()
    if "nigeria" in t or "nigerian" in t: return "Nigeria mentioned directly -- check official eligibility"
    if any(x in t for x in ["africa","african students","sub-saharan","developing countries","developing country"]):
        return "Africa/developing countries mentioned -- check official eligibility"
    return "Not stated -- verify eligibility"

def detail_links(soup):
    out={}
    for a in soup.find_all("a",href=True):
        url=urljoin(BASE,a["href"]).split("?")[0].rstrip("/")
        if not url.startswith(BASE+"/scholarships/"): continue
        slug=url[len(BASE+"/scholarships/"):]
        title=a.get_text(" ",strip=True)
        if slug and "/" not in slug and len(title)>=8 and title.lower() not in {"scholarships","view scholarship","apply now","read more"}:
            out[url]=title
    return out

def scrape(url, level):
    try:
        r=requests.get(url,headers=HEADERS,timeout=30); r.raise_for_status()
    except Exception as e:
        print("FAILED:",url,e); return []
    soup=BeautifulSoup(r.text,"html.parser")
    links=detail_links(soup)
    print(url, "->", len(links), "scholarships")
    rows=[]
    for u,title in links.items():
        a=soup.find("a",href=lambda h: h and urljoin(BASE,h).split("?")[0].rstrip("/")==u)
        node=a
        for _ in range(4):
            if node and node.parent: node=node.parent
        text=node.get_text(" ",strip=True) if node else title
        d=deadline(text)
        rows.append({"id":make_id(u),"type":"scholarship","source_platform":"ScholarshipTab","title":title,"company":"","location":"","salary":"","level":level,"sponsorship_status":funding(text),"ielts_status":ielts(text),"nigeria_note":eligibility(text),"application_status":status(d),"source_url":u,"date_posted":"","deadline":d,"date_scraped":TODAY.isoformat(),"search_term":url,"notes":""})
    return rows

def main():
    try:
        with open(DATA_FILE,encoding="utf-8") as f: existing=json.load(f)
        if not isinstance(existing,list): existing=[]
    except Exception:
        existing=[]
    ids={str(x.get("id")) for x in existing if x.get("id")}
    fetched=[]
    for url,level in LISTING_PAGES:
        fetched += scrape(url,level); time.sleep(1)
    if not fetched:
        print("ERROR: zero scholarships fetched; existing data was NOT changed.")
        raise SystemExit(2)
    unique={x["id"]:x for x in fetched}
    new=[x for k,x in unique.items() if k not in ids]
    combined=existing+new
    os.makedirs(os.path.dirname(DATA_FILE),exist_ok=True)
    tmp=DATA_FILE+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(combined,f,indent=2,ensure_ascii=False)
    os.replace(tmp,DATA_FILE)
    print("Fetched:",len(fetched),"New:",len(new),"Total scholarships:",sum(x.get("type")=="scholarship" for x in combined))

if __name__=="__main__":
    main()
