import datetime as dt, hashlib, json, os, re, requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

TODAY=dt.date.today()
DATA_FILE=os.path.join(os.path.dirname(__file__),"..","data","listings.json")
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; OpportunityDesk/1.0)"}

OFFICIAL=[
("Australia Awards Scholarships","Mixed / Fully Funded","Australia","Australian Government","https://www.dfat.gov.au/people-to-people/australia-awards/australia-awards-scholarships","30 April 2026","FULLY FUNDED","Nigeria is listed among participating African countries; verify country-specific criteria."),
("Erasmus Mundus Joint Masters","Masters","Europe / Multiple countries","Erasmus+","https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters","","FULLY FUNDED","Students worldwide are welcome; programme-specific eligibility applies."),
("Chevening Scholarships","Masters","United Kingdom","UK Government / Chevening","https://www.gov.uk/postgraduate-scholarships-international-students","","FULLY FUNDED","International applicants meeting Chevening requirements; check the current round."),
("Commonwealth Scholarships","Masters / PhD","United Kingdom","UK Government / Commonwealth Scholarship Commission","https://www.gov.uk/guidance/foreign-commonwealth-development-office-international-scholarship-programmes","","FULLY FUNDED","Eligible Commonwealth citizens/residents; programme-specific criteria apply."),
("DAAD Scholarship Database","Mixed / Fully Funded","Germany / International","DAAD","https://www2.daad.de/deutschland/stipendium/datenbank/en/21148-scholarship-database/","","UNCLEAR","Eligibility varies by programme and applicant country."),
("EduCanada Scholarships for International Applicants","Mixed / Fully Funded","Canada","EduCanada / Government of Canada","https://www.educanada.ca/scholarships-bourses/non_can/index.aspx?lang=eng","","UNCLEAR","Eligibility varies by programme and country."),
("Study with New Zealand — Scholarships","Mixed","New Zealand","Study with New Zealand","https://www.studywithnewzealand.govt.nz/en/study-options/scholarships","","UNCLEAR","Eligibility varies by scholarship and nationality."),
]
AGG=["https://www.scholarshiptab.com/undergraduate","https://www.scholarshiptab.com/masters","https://www.scholarshiptab.com/phd"]

def iid(u): return hashlib.sha1(u.encode()).hexdigest()[:12]
def dparse(v):
    if not v:return None
    m=re.search(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",v,re.I)
    if not m:return None
    months={x:i for i,x in enumerate("January February March April May June July August September October November December".split(),1)}
    try:return dt.date(int(m.group(3)),months[m.group(2).capitalize()],int(m.group(1)))
    except:return None
def status(v):
    d=dparse(v); return "UNKNOWN" if not d else ("OPEN" if d>=TODAY else "CLOSED")
def official():
    out=[]
    for title,level,loc,source,url,deadline,funding,elig in OFFICIAL:
        out.append({"id":iid(url),"type":"scholarship","source_platform":source,"title":title,"company":"","location":loc,"salary":"","level":level,"sponsorship_status":funding,"ielts_status":"NOT STATED","nigeria_note":elig,"application_status":status(deadline),"source_url":url,"date_posted":"","deadline":deadline,"date_scraped":TODAY.isoformat(),"search_term":"official scholarship source","notes":"Verify all eligibility and deadline details on the official page."})
    return out
def optional_tab():
    out=[]
    for page in AGG:
        try:
            r=requests.get(page,headers=HEADERS,timeout=20)
            if r.status_code==403:
                print("SKIP ScholarshipTab 403:",page); continue
            r.raise_for_status()
            soup=BeautifulSoup(r.text,"html.parser")
            for a in soup.find_all("a",href=True):
                u=urljoin(page,a["href"]).split("?")[0].rstrip("/")
                t=a.get_text(" ",strip=True)
                if "/scholarships/" not in u or len(t)<8:continue
                out.append({"id":iid(u),"type":"scholarship","source_platform":"ScholarshipTab","title":t,"company":"","location":"","salary":"","level":"Scholarship","sponsorship_status":"UNCLEAR","ielts_status":"NOT STATED","nigeria_note":"Verify eligibility on official page.","application_status":"UNKNOWN","source_url":u,"date_posted":"","deadline":"","date_scraped":TODAY.isoformat(),"search_term":page,"notes":"Aggregator listing; verify details on the official page."})
        except Exception as e: print("SKIP ScholarshipTab:",e)
    return out
def main():
    try:
        with open(DATA_FILE,encoding="utf-8") as f: existing=json.load(f)
        if not isinstance(existing,list):existing=[]
    except:existing=[]
    rows=[];seen=set()
    for x in existing+official()+optional_tab():
        k=str(x.get("id") or iid(str(x.get("source_url",""))))
        if k not in seen:seen.add(k);rows.append(x)
    os.makedirs(os.path.dirname(DATA_FILE),exist_ok=True)
    with open(DATA_FILE,"w",encoding="utf-8") as f:json.dump(rows,f,indent=2,ensure_ascii=False)
    print("Scholarship updater completed successfully.")
    print("Official records:",len(official()))
    print("Total scholarship records:",sum(x.get("type")=="scholarship" for x in rows))
if __name__=="__main__":main()
