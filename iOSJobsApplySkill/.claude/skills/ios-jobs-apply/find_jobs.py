#!/usr/bin/env python3
"""Find recent US iOS roles on public job-board APIs (stdlib only).

Usage:
  python3 find_jobs.py                 # candidates as JSON on stdout
  python3 find_jobs.py --days 7 --max-yoe 3
  python3 find_jobs.py --check         # verify every board in companies.json responds
"""
import argparse
import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
COMPANIES = SKILL_DIR / "companies.json"
APPLICATIONS = SKILL_DIR.parents[2] / "applications.json"  # iOSJobsApplySkill/applications.json
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) ios-jobs-apply/1.0", "Accept": "application/json"}
NOW = datetime.now(timezone.utc)

TITLE_RE = re.compile(r"\b(iOS|iPadOS|Swift|SwiftUI|Mobile|iPhone)\b", re.I)
IOS_DESC_RE = re.compile(r"\b(iOS|Swift|SwiftUI|UIKit|Objective-C)\b", re.I)
ANDROID_ONLY_RE = re.compile(r"\bAndroid\b", re.I)
EXCLUDE_TITLE_RE = re.compile(r"\b(Staff|Principal|Lead|Manager|Director|Head of|Intern|Internship|Distinguished|Architect)\b", re.I)
NON_ENG_RE = re.compile(r"\b(Marketing|Designer|Recruit\w*|Sales|Account|Analyst|PMT|Product Manager)\b", re.I)
YOE_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)", re.I)
US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
US_WORDS_RE = re.compile(r"United States|\bUSA\b|\bU\.S\.|\bUS\b|Remote\s*[-–(,]?\s*US|San Francisco|New York|Seattle|Los Angeles|Palo Alto|Mountain View|Menlo Park|Sunnyvale|Cupertino|Bellevue|Austin|Boston|Chicago|Denver|Atlanta", re.I)


def fetch(url, data=None, timeout=25):
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


def min_yoe(desc):
    nums = [int(m.group(1)) for m in YOE_RE.finditer(desc or "") if 0 < int(m.group(1)) <= 20]
    return min(nums) if nums else None


def days_ago(dt):
    return (NOW - dt).total_seconds() / 86400 if dt else None


def iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def title_reject_reason(title, desc=None):
    """None if the title looks like an in-scope iOS engineering role, else why not."""
    if not TITLE_RE.search(title):
        return "not an iOS title"
    m = EXCLUDE_TITLE_RE.search(title)
    if m:
        return f"level/type excluded ({m.group(1)})"
    if NON_ENG_RE.search(title):
        return "not an engineering role"
    if ANDROID_ONLY_RE.search(title) and not re.search(r"\biOS\b", title, re.I):
        return "Android-only"
    if re.search(r"\bMobile\b", title, re.I) and not re.search(r"\b(iOS|Swift)\b", title, re.I):
        if desc is not None and not IOS_DESC_RE.search(desc):
            return "mobile role with no iOS/Swift in description"
    return None


def title_ok(title, desc=None):
    return title_reject_reason(title, desc) is None


# ---- one function per ATS; each returns a list of normalized job dicts ----

def greenhouse(c):
    d = fetch(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    out = []
    for j in d["jobs"]:
        posted = iso(j.get("first_published")) or iso(j.get("updated_at"))
        out.append(dict(id=f"gh-{j['id']}", title=j["title"].strip(), location=j["location"]["name"],
                        posted=posted, dateSource="first_published" if j.get("first_published") else "updated_at",
                        url=j["absolute_url"], applyUrl=f"https://job-boards.greenhouse.io/{c['slug']}/jobs/{j['id']}",
                        desc=text(j.get("content"))))
    return out


def ashby(c):
    d = fetch(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}?includeCompensation=false")
    out = []
    for j in d["jobs"]:
        if not j.get("isListed", True):
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
        countries = [((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")]
        countries += [((s.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "") for s in j.get("secondaryLocations", [])]
        loc = "; ".join(l for l in locs if l)
        if any(x in ("USA", "United States") for x in countries):
            loc += " (US)"
        out.append(dict(id=f"ashby-{j['id']}", title=j["title"].strip(), location=loc, posted=iso(j.get("publishedAt")),
                        dateSource="publishedAt", url=j["jobUrl"], applyUrl=j.get("applyUrl", j["jobUrl"]),
                        desc=j.get("descriptionPlain", "")))
    return out


def lever(c):
    d = fetch(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    out = []
    for j in d:
        cat = j.get("categories", {})
        loc = "; ".join(cat.get("allLocations") or [cat.get("location", "")])
        if j.get("country") == "US":
            loc += " (US)"
        desc = j.get("descriptionPlain", "") + " " + " ".join(text(l.get("content")) for l in j.get("lists", []))
        out.append(dict(id=f"lever-{j['id']}", title=j["text"].strip(), location=loc,
                        posted=datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc), dateSource="createdAt",
                        url=j["hostedUrl"], applyUrl=j.get("applyUrl", j["hostedUrl"]), desc=desc))
    return out


def workday(c, days):
    base = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"
    out = []
    for q in ("iOS", "Swift"):
        offset = 0
        while offset < 200:
            d = fetch(f"{base}/jobs", {"searchText": q, "limit": 20, "offset": offset, "appliedFacets": {}})
            posts = d.get("jobPostings", [])
            for p in posts:
                age = parse_workday_age(p.get("postedOn", ""))
                if age is None or age > days or not title_ok(p["title"]):
                    continue
                info = fetch(base + p["externalPath"]).get("jobPostingInfo", {})
                loc = "; ".join([info.get("location", "")] + info.get("additionalLocations", []))
                if info.get("country", {}).get("descriptor") == "United States of America":
                    loc += " (US)"
                out.append(dict(id=f"wd-{c['tenant']}-{info.get('jobReqId', p['externalPath'])}", title=p["title"],
                                location=loc, posted=datetime.fromtimestamp(NOW.timestamp() - age * 86400, timezone.utc),
                                dateSource="postedOn:" + p.get("postedOn", ""),
                                url=info.get("externalUrl") or f"https://{c['host']}/en-US/{c['site']}{p['externalPath']}",
                                applyUrl=info.get("externalUrl"), desc=text(info.get("jobDescription"))))
            if len(posts) < 20:
                break
            offset += 20
    return out


def parse_workday_age(s):
    s = s.lower()
    if "today" in s:
        return 0
    if "yesterday" in s:
        return 1
    m = re.search(r"(\d+)\+?\s*days", s)
    return int(m.group(1)) if m else None


def amazon(c, days):
    out = []
    offset = 0
    while offset < 300:
        d = fetch(f"https://www.amazon.jobs/en/search.json?base_query=iOS&country=USA&sort=recent&result_limit=100&offset={offset}")
        jobs = d.get("jobs", [])
        for j in jobs:
            try:
                posted = datetime.strptime(j["posted_date"], "%B %d, %Y").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                posted = None
            out.append(dict(id=f"amzn-{j['id_icims']}", title=j["title"], location=j.get("location", "") + " (US)",
                            posted=posted, dateSource="posted_date", url="https://www.amazon.jobs" + j["job_path"],
                            applyUrl=j.get("url_next_step"),
                            desc=text(j.get("basic_qualifications", "")) + " " + text(j.get("description", ""))))
        if len(jobs) < 100 or (out and out[-1]["posted"] and days_ago(out[-1]["posted"]) > days):
            break
        offset += 100
    return out


def eightfold(c, days):
    base = f"https://{c['host']}/api/apply/v2/jobs"
    d = fetch(f"{base}?domain={c['domain']}&query=iOS&num=50&sort_by=new")
    out = []
    for p in d.get("positions", []):
        posted = datetime.fromtimestamp(p["t_create"], timezone.utc) if p.get("t_create") else None
        if not title_ok(p["name"]) or (posted and days_ago(posted) > days):
            continue
        detail = fetch(f"{base}/{p['id']}?domain={c['domain']}")
        out.append(dict(id=f"ef-{p['id']}", title=p["name"], location="; ".join(p.get("locations") or [p.get("location", "")]),
                        posted=posted, dateSource="t_create", url=p.get("canonicalPositionUrl"),
                        applyUrl=p.get("canonicalPositionUrl"), desc=text(detail.get("job_description"))))
    return out


NO_LOGIN = {"greenhouse", "ashby", "lever"}


def run_company(c, days):
    ats = c["ats"]
    if ats in ("workday", "amazon", "eightfold"):
        jobs = {"workday": workday, "amazon": amazon, "eightfold": eightfold}[ats](c, days)
    else:
        jobs = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever}[ats](c)
    for j in jobs:
        j["company"], j["ats"] = c["name"], ats
        j["applyType"] = "no login" if ats in NO_LOGIN else "login needed"
    return jobs


def check(companies):
    def probe(c):
        try:
            n = len(run_company(c, days=7))
            return c["name"], "ok", n
        except Exception as e:  # noqa: BLE001 - report any failure per board
            return c["name"], f"FAIL {type(e).__name__}: {e}", 0
    with ThreadPoolExecutor(8) as ex:
        for name, status, n in ex.map(probe, companies):
            print(f"{name:<10} {status}  ({n} jobs fetched)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--max-yoe", type=int, default=3)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    companies = json.loads(COMPANIES.read_text())["api"]
    if args.check:
        return check(companies)

    applied = set()
    if APPLICATIONS.exists():
        for a in json.loads(APPLICATIONS.read_text() or "[]"):
            applied.update(filter(None, [a.get("roleId"), a.get("url")]))

    candidates, rejected, errors = [], [], []

    def safe(c):
        try:
            return run_company(c, args.days)
        except Exception as e:  # noqa: BLE001
            errors.append({"company": c["name"], "error": f"{type(e).__name__}: {e}"})
            return []

    seen = set()
    with ThreadPoolExecutor(8) as ex:
        for jobs in ex.map(safe, companies):
            for j in jobs:
                if j["id"] in seen or not TITLE_RE.search(j["title"]):
                    continue  # duplicate, or not an iOS-ish title at all
                seen.add(j["id"])
                age = days_ago(j["posted"])
                if age is None or age > args.days:
                    continue  # too old (or undated); not worth listing
                yoe = min_yoe(j["desc"])
                reason = title_reject_reason(j["title"], j["desc"])
                if reason:
                    pass
                elif not is_us(j["location"]):
                    reason = f"not US ({j['location']})"
                elif yoe is not None and yoe > args.max_yoe:
                    reason = f"requires {yoe}+ years"
                elif j["id"] in applied or j["url"] in applied:
                    reason = "already applied"
                row = {k: j[k] for k in ("company", "title", "location", "url", "applyUrl", "applyType", "ats", "id", "dateSource")}
                row["posted"] = j["posted"].date().isoformat()
                row["minYoe"] = yoe
                (rejected if reason else candidates).append({**row, **({"reason": reason} if reason else {})})

    candidates.sort(key=lambda r: r["posted"] or "", reverse=True)
    json.dump({"generatedAt": NOW.isoformat(timespec="seconds"), "days": args.days, "maxYoe": args.max_yoe,
               "candidates": candidates, "rejected": rejected, "errors": errors}, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
