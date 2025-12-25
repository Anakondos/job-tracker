# parsers/workday.py
"""
Workday ATS Parser

Workday uses a hidden JSON API to fetch job postings.
URL format: https://{company}.{wd_version}.myworkdayjobs.com/wday/cxs/{company}/{site_id}/jobs
"""

import requests
from typing import Optional, List, Dict


def fetch_workday(company: str, api_url: str, base_url: Optional[str] = None, limit: int = 500) -> List[Dict]:
    """Fetch jobs from Workday API."""
    
    # Extract domain and site_id from URL
    domain = ""
    site_id = ""
    
    if "/wday/cxs/" in api_url:
        domain = api_url.split("/wday/")[0]
        parts = api_url.split("/wday/cxs/")[1].split("/")
        if len(parts) >= 2:
            site_id = parts[1]
    
    if not base_url and domain and site_id:
        base_url = f"{domain}/{site_id}"
    
    # Full headers for each request (important for Workday!)
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": domain,
        "Referer": f"{domain}/{site_id}" if site_id else domain,
    }
    
    session = requests.Session()
    jobs = []
    offset = 0
    batch_size = 50
    
    while offset < limit:
        payload = {
            "appliedFacets": {},
            "limit": batch_size,
            "offset": offset,
            "searchText": ""
        }
        
        try:
            r = session.post(api_url, json=payload, headers=headers, timeout=30)
            if r.status_code != 200:
                print(f"[Workday] HTTP {r.status_code} for {company}")
                break
            data = r.json()
        except requests.RequestException as e:
            print(f"[Workday] Error fetching {company}: {e}")
            break
        
        postings = data.get("jobPostings", [])
        total = data.get("total", 0)
        
        if not postings:
            break
        
        for p in postings:
            title = p.get("title", "")
            loc_text = p.get("locationsText", "") or ", ".join(p.get("bulletFields", []))
            external_path = p.get("externalPath", "")
            job_url = f"{base_url}{external_path}" if base_url and external_path else (base_url or api_url)
            posted_on = p.get("postedOn", "") or p.get("postedOnDate", "")
            
            jobs.append({
                "company": company,
                "title": title,
                "location": loc_text,
                "job_url": job_url,
                "updated_at": posted_on,
                "ats": "workday",
            })
        
        offset += batch_size
        if offset >= total:
            break
    
    return jobs


if __name__ == "__main__":
    print("Testing Capital One Workday...")
    jobs = fetch_workday(
        company="Capital One",
        api_url="https://capitalone.wd12.myworkdayjobs.com/wday/cxs/capitalone/Capital_One/jobs",
        limit=20
    )
    print(f"Fetched {len(jobs)} jobs")
    
    pm_jobs = [j for j in jobs if 'product' in j['title'].lower() or 'program' in j['title'].lower()]
    print(f"PM/Program roles: {len(pm_jobs)}")
    for j in pm_jobs[:5]:
        print(f"  - {j['title']} | {j['location']}")
