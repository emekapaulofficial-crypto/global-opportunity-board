/**
 * fetch-jobs.js
 * Pulls fresh remote job listings from FREE, no-key-required public APIs
 * and merges them with a curated static list of visa/scholarship/fellowship/
 * grant/course/travel/health opportunities (these don't have reliable free
 * public APIs, so they're maintained by hand below — edit anytime).
 *
 * Runs automatically every day via .github/workflows/update-jobs.yml
 * Free APIs used: RemoteOK, Remotive, Arbeitnow, Himalayas — all public,
 * no signup, no API key, no cost.
 */

const fs = require("fs");

const CURATED = [
  { type:"visa", role:"Skilled Worker Visa Sponsorship List", company:"UK Home Office — licensed sponsors", location:"United Kingdom", deadline:"Ongoing", link:"https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers" },
  { type:"visa", role:"Canada Express Entry — Job Bank", company:"Government of Canada", location:"Canada", deadline:"Ongoing", link:"https://www.jobbank.gc.ca" },
  { type:"visa", role:"Germany Job Seeker Visa Roles", company:"Make it in Germany", location:"Germany", deadline:"Ongoing", link:"https://www.make-it-in-germany.com" },
  { type:"scholar", role:"Türkiye Bursları Scholarship", company:"Government of Turkey", location:"Turkey", deadline:"Jan–Feb (annual)", link:"https://www.turkiyeburslari.gov.tr" },
  { type:"scholar", role:"Stipendium Hungaricum", company:"Government of Hungary", location:"Hungary", deadline:"Jan (annual)", link:"https://stipendiumhungaricum.hu" },
  { type:"scholar", role:"DAAD Scholarships", company:"German Academic Exchange Service", location:"Germany", deadline:"Varies by program", link:"https://www.daad.de/en" },
  { type:"scholar", role:"MEXT Scholarship", company:"Government of Japan", location:"Japan", deadline:"Apr–May (annual)", link:"https://www.mext.go.jp/en/" },
  { type:"fellow", role:"Mandela Washington Fellowship", company:"U.S. Department of State", location:"United States", deadline:"Annual — check site", link:"https://mwfellowship.org" },
  { type:"fellow", role:"Mastercard Foundation Scholars Program", company:"Mastercard Foundation", location:"Multiple countries", deadline:"Varies by partner", link:"https://mastercardfdn.org/all/scholars/" },
  { type:"grant", role:"Global Innovation Fund Grants", company:"Global Innovation Fund", location:"Worldwide", deadline:"Rolling", link:"https://www.globalinnovation.fund" },
  { type:"course", role:"Free Certificate Courses", company:"Saylor Academy", location:"Online — Worldwide", deadline:"Always open", link:"https://www.saylor.org" },
  { type:"course", role:"Free Coding Certifications", company:"freeCodeCamp", location:"Online — Worldwide", deadline:"Always open", link:"https://www.freecodecamp.org" },
  { type:"travel", role:"Working Holiday & Cultural Exchange Programs", company:"Various host countries", location:"Multiple countries", deadline:"Rolling", link:"https://www.goabroad.com" },
  { type:"health", role:"Overseas Nursing & Care Worker Sponsorship", company:"UK NHS & private care providers", location:"United Kingdom", deadline:"Rolling", link:"https://www.healthcareers.nhs.uk" },
  { type:"health", role:"Global Health Fellowships", company:"World Health Organization", location:"Multiple countries", deadline:"Varies", link:"https://www.who.int/careers" },
];

async function safeFetch(url, opts = {}) {
  try {
    const res = await fetch(url, { headers: { "User-Agent": "TheBoard/1.0" }, ...opts });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    console.error("Fetch failed:", url, e.message);
    return null;
  }
}

async function getRemoteOK() {
  const data = await safeFetch("https://remoteok.com/api");
  if (!Array.isArray(data)) return [];
  return data
    .filter(j => j.position)
    .slice(0, 15)
    .map(j => ({
      type: "remote",
      role: j.position,
      company: j.company || "Remote OK listing",
      location: j.location && j.location.trim() ? j.location : "Worldwide",
      deadline: "Rolling",
      link: j.url || "https://remoteok.com",
    }));
}

async function getRemotive() {
  const data = await safeFetch("https://remotive.com/api/remote-jobs?limit=15");
  if (!data || !Array.isArray(data.jobs)) return [];
  return data.jobs.map(j => ({
    type: "remote",
    role: j.title,
    company: j.company_name || "Remotive listing",
    location: j.candidate_required_location || "Worldwide",
    deadline: "Rolling",
    link: j.url,
  }));
}

async function getArbeitnow() {
  const data = await safeFetch("https://www.arbeitnow.com/api/job-board-api");
  if (!data || !Array.isArray(data.data)) return [];
  return data.data.slice(0, 15).map(j => ({
    type: j.visa_sponsorship ? "visa" : "remote",
    role: j.title,
    company: j.company_name || "Arbeitnow listing",
    location: j.location || "Worldwide",
    deadline: "Rolling",
    link: j.url,
  }));
}

async function getHimalayas() {
  const data = await safeFetch("https://himalayas.app/jobs/api?limit=15");
  const list = data && (data.jobs || data.data);
  if (!Array.isArray(list)) return [];
  return list.map(j => ({
    type: "remote",
    role: j.title,
    company: (j.companyName || j.company || {}).name || j.companyName || "Himalayas listing",
    location: (j.locationRestrictions && j.locationRestrictions.join(", ")) || "Worldwide",
    deadline: "Rolling",
    link: j.applicationLink || j.link || "https://himalayas.app",
  }));
}

async function main() {
  console.log("Fetching from free job APIs...");
  const [remoteok, remotive, arbeitnow, himalayas] = await Promise.all([
    getRemoteOK(), getRemotive(), getArbeitnow(), getHimalayas(),
  ]);

  const live = [...remoteok, ...remotive, ...arbeitnow, ...himalayas]
    .filter(j => j.role && j.link);

  const all = [...live, ...CURATED];

  const output = {
    updated: new Date().toUTCString(),
    jobs: all,
  };

  fs.writeFileSync("jobs.json", JSON.stringify(output, null, 2));
  console.log(`Wrote ${all.length} listings (${live.length} live + ${CURATED.length} curated) to jobs.json`);
}

main();
