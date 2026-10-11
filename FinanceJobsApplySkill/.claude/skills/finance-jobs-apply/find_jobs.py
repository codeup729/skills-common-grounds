#!/usr/bin/env python3
"""Find recent US Financial Advisor / Private Bank Analyst-Associate roles on public job feeds (stdlib only).

Drops any posting whose text says the employer will not sponsor work visas.

Usage:
  python3 find_jobs.py                 # candidates as JSON on stdout
  python3 find_jobs.py --days 7
  python3 find_jobs.py --check         # verify every feed in companies.json responds
"""
import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
COMPANIES = SKILL_DIR / "companies.json"
APPLICATIONS = SKILL_DIR.parents[2] / "applications.json"  # FinanceJobsApplySkill/applications.json
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) finance-jobs-apply/1.0", "Accept": "application/json"}
NOW = datetime.now(timezone.utc)

# Role family 1: Financial Advisor (and firm-specific equivalents).
ADVISOR_RE = re.compile(r"\b(Financial (Advisor|Adviser|Consultant|Planner|Solutions Advisor)|Wealth (Advisor|Adviser|Planner)|Private Client Advisor|Investment Consultant|Advisor (Associate|Development|Trainee))\b", re.I)
# Role family 2: Private Bank / Private Wealth analyst or associate.
PRIVATE_RE = re.compile(r"\b(Private Bank(ing|er)?|Private Wealth|Private Client|Wealth Management|Wealth)\b", re.I)
JUNIOR_RE = re.compile(r"\b(Analyst|Associate)\b", re.I)
EXCLUDE_TITLE_RE = re.compile(r"\b(Vice President|VP|Director|Managing Director|Executive Director|Head|Manager|Team Lead|Market Leader|Intern|Internship|Summer|Chief|Principal|Technology|Software|Engineer|Developer|Data|Cyber|Audit)\b", re.I)
NO_SPONSOR_RE = re.compile(
    r"(not|unable to|cannot|can ?not|will not|won'?t|does not|do not|is not able to|are not able to)\s+(currently\s+)?(be\s+)?(able\s+to\s+)?(provide|offer|sponsor|support)\w*\s+(\w+\s+){0,4}?(visa|sponsorship|immigration|work authorization)"
    r"|no\s+(visa\s+)?sponsorship"
    r"|sponsorship\s+(is\s+)?(not\s+)?(available|offered|provided)\s*(for this (role|position))?\s*[:.]?\s*no\b"
    r"|without\s+(the\s+need\s+for\s+)?(current\s+or\s+future\s+)?(employer\s+|visa\s+)?sponsorship"
    r"|not\s+eligible\s+for\s+(visa\s+)?sponsorship"
    r"|sponsorship\s+(is\s+)?not\s+(available|offered|provided)"
    r"|not\s+(\w+[\s,]+){0,8}?require[^.]{0,80}sponsorship"
    r"|(ineligible|not\s+eligible)\s+for\s+[^.]{0,30}sponsorship"
    r"|(do|does|will)\s+not\s+(offer|provide)\s+[^.]{0,40}(visa|sponsorship)",
    re.I)
YOE_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)", re.I)
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
US_WORDS_RE = re.compile(r"United States|\bUSA\b|\bU\.S\.|\bUS\b|New York|Boston|Chicago|Charlotte|Dallas|Houston|Miami|San Francisco|Los Angeles|Atlanta|Philadelphia|Jersey City|Purchase|Stamford", re.I)


def fetch(url, data=None, timeout=30):
    headers = dict(UA)
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def text(s):
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def is_us(loc):
    if not loc:
        return False
    if US_WORDS_RE.search(loc):
        return True
    return any(tok in US_STATES for tok in re.findall(r"\b[A-Z]{2}\b", loc))


def family(title):
    """'advisor', 'private-bank', or None."""
    if EXCLUDE_TITLE_RE.search(title):
        return None
    if ADVISOR_RE.search(title):
        return "advisor"
    if PRIVATE_RE.search(title) and JUNIOR_RE.search(title):
        return "private-bank"
    return None


def no_sponsor_snippet(desc):
    m = NO_SPONSOR_RE.search(desc or "")
    if not m:
        return None
    a, b = max(0, m.start() - 60), min(len(desc), m.end() + 60)
    return "…" + desc[a:b].strip() + "…"


def min_yoe(desc):
    nums = [int(m.group(1)) for m in YOE_RE.finditer(desc or "") if 0 < int(m.group(1)) <= 20]
    return min(nums) if nums else None


def days_ago(dt):
    return (NOW - dt).total_seconds() / 86400 if dt else None


def parse_workday_age(s):
    s = (s or "").lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*days", s)
    return int(m.group(1)) if m else None


# ---- feeds: each returns normalized job dicts for title-matching, recent postings ----

def workday(c, terms, days):
    base = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"
    job_base = c.get("jobBase", f"https://{c['host']}/en-US/{c['site']}")
    seen, out = set(), []
    for q in terms:
        offset = 0
        while offset < 200:
            d = fetch(f"{base}/jobs", {"searchText": q, "limit": 20, "offset": offset, "appliedFacets": {}})
            posts = d.get("jobPostings", [])
            stop = False
            for p in posts:
                age = parse_workday_age(p.get("postedOn"))
                if age is not None and age > days:
                    stop = True  # results aren't strictly date-sorted, but older pages rarely matter
                    continue
                path = p.get("externalPath")
                if age is None or path in seen or not family(p.get("title", "")):
                    continue
                seen.add(path)
                info = fetch(base + path).get("jobPostingInfo", {})
                loc = "; ".join([info.get("location", "")] + info.get("additionalLocations", []))
                if (info.get("country") or {}).get("descriptor") == "United States of America":
                    loc += " (US)"
                out.append(dict(id=f"wd-{c['tenant']}-{info.get('jobReqId', path)}", title=p["title"], location=loc,
                                posted=datetime.fromtimestamp(NOW.timestamp() - age * 86400, timezone.utc),
                                dateSource="postedOn:" + p.get("postedOn", ""),
                                url=info.get("externalUrl") or job_base + path, desc=text(info.get("jobDescription"))))
            if len(posts) < 20 or stop:
                break
            offset += 20
    return out


def oracle(c, terms, days):
    base = f"https://{c['host']}/hcmRestApi/resources/latest"
    seen, out = set(), []
    for q in terms:
        kw = urllib.parse.quote(f'"{q}"')
        url = (f"{base}/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations"
               f"&finder=findReqs;siteNumber={c['site']},limit=50,keyword={kw},sortBy=POSTING_DATES_DESC")
        items = fetch(url).get("items") or [{}]
        for j in items[0].get("requisitionList", []):
            try:
                posted = datetime.fromisoformat(j["PostedDate"]).replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if days_ago(posted) > days + 1 or j["Id"] in seen or not family(j.get("Title", "")):
                continue
            seen.add(j["Id"])
            locs = [j.get("PrimaryLocation", "")] + [s.get("Name", "") for s in j.get("secondaryLocations", [])]
            det = fetch(f"{base}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                        f"&finder=ById;Id=%22{j['Id']}%22,siteNumber={c['site']}").get("items") or [{}]
            desc = " ".join(text(det[0].get(k)) for k in ("ExternalDescriptionStr", "ExternalQualificationsStr", "ExternalResponsibilitiesStr"))
            out.append(dict(id=f"oracle-{j['Id']}", title=j["Title"], location="; ".join(l for l in locs if l), posted=posted,
                            dateSource="PostedDate", url=f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{j['Id']}",
                            desc=desc))
    return out


FEEDS = {"workday": workday, "oracle": oracle}


def run_company(c, terms, days):
    jobs = FEEDS[c["ats"]](c, terms, days)
    for j in jobs:
        j["company"], j["ats"] = c["name"], c["ats"]
        j["note"] = c.get("note")
    return jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(COMPANIES.read_text())
    companies, terms = cfg["api"], cfg["searchTerms"]

    if args.check:
        def probe(c):
            try:
                return c["name"], "ok", len(run_company(c, terms[:1], args.days))
            except Exception as e:  # noqa: BLE001 - report any failure per feed
                return c["name"], f"FAIL {type(e).__name__}: {e}", 0
        with ThreadPoolExecutor(8) as ex:
            for name, status, n in ex.map(probe, companies):
                print(f"{name:<16} {status}  ({n} matching '{terms[0]}' in last {args.days:g} days)")
        return

    applied = set()
    if APPLICATIONS.exists():
        for a in json.loads(APPLICATIONS.read_text() or "[]"):
            applied.update(filter(None, [a.get("roleId"), a.get("url")]))

    candidates, rejected, errors = [], [], []

    def safe(c):
        try:
            return run_company(c, terms, args.days)
        except Exception as e:  # noqa: BLE001
            errors.append({"company": c["name"], "error": f"{type(e).__name__}: {e}"})
            return []

    with ThreadPoolExecutor(6) as ex:
        for jobs in ex.map(safe, companies):
            for j in jobs:
                age = days_ago(j["posted"])
                if age is None or age > args.days:
                    continue
                snippet = no_sponsor_snippet(j["desc"])
                reason = None
                if not is_us(j["location"]):
                    reason = f"not US ({j['location'][:80]})"
                elif snippet:
                    reason = f"no sponsorship: {snippet}"
                elif j["id"] in applied or j["url"] in applied:
                    reason = "already applied"
                row = {k: j[k] for k in ("company", "title", "location", "url", "ats", "id", "dateSource", "note")}
                row.update(family=family(j["title"]), posted=j["posted"].date().isoformat(), minYoe=min_yoe(j["desc"]),
                           mentionsSponsorship=bool(re.search(r"sponsor", j["desc"], re.I)))
                (rejected if reason else candidates).append({**row, **({"reason": reason} if reason else {})})

    candidates.sort(key=lambda r: (r["family"] or "", r["posted"]), reverse=True)
    json.dump({"generatedAt": NOW.isoformat(timespec="seconds"), "days": args.days,
               "candidates": candidates, "rejected": rejected, "errors": errors}, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
