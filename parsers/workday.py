# parsers/workday.py
"""
Workday ATS Parser

Workday uses a hidden JSON API to fetch job postings.
The API endpoint is: https://{tenant}.{wd_version}.myworkdayjobs.com/wday/cxs/{tenant}/{site_id}/jobs

Supported banks and fintech companies with known Workday URLs are defined in
data/companies.json with ats="workday".
"""

import re
import requests
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple
from urllib.parse import urlparse


def parse_workday_url(board_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse Workday board URL to extract components for API call.
    
    Input formats:
      - https://capitalone.wd12.myworkdayjobs.com/Capital_One
      - https://pnc.wd5.myworkdayjobs.com/External
      - https://wf.wd1.myworkdayjobs.com/WellsFargoJobs
      - https://company.wd1.myworkdayjobs.com/en-US/SiteId  (with locale)
    
    Returns:
      (api_url, base_url, site_id) or (None, None, None) if parsing fails
    """
    try:
        parsed = urlparse(board_url)
        host = parsed.netloc  # e.g., capitalone.wd12.myworkdayjobs.com
        
        # Extract tenant from host (first part before .wd)
        tenant_match = re.match(r'^([^.]+)\.wd\d+\.myworkdayjobs\.com$', host)
        if not tenant_match:
            return None, None, None
        tenant = tenant_match.group(1)
        
        # Extract site_id from path (last non-empty segment, skip locale like en-US)
        path_parts = [p for p in parsed.path.strip('/').split('/') if p]
        
        # Skip locale prefixes like "en-US", "en", etc.
        if path_parts and re.match(r'^[a-z]{2}(-[A-Z]{2})?$', path_parts[0]):
            path_parts = path_parts[1:]
        
        if not path_parts:
            return None, None, None
            
        site_id = path_parts[0]
        
        # Construct API URL
        origin = f"https://{host}"
        api_url = f"{origin}/wday/cxs/{tenant}/{site_id}/jobs"
        base_url = f"{origin}/{site_id}"
        
        return api_url, base_url, site_id
        
    except Exception as e:
        print(f"[Workday] URL parse error: {e}")
        return None, None, None


def parse_posted_on(posted_on: str) -> Optional[str]:
    """
    Convert Workday's relative date string to ISO format.
    
    Examples:
      "Posted Today" -> today's date
      "Posted Yesterday" -> yesterday's date
      "Posted 2 Days Ago" -> 2 days ago
      "Posted 30+ Days Ago" -> 30 days ago
    """
    if not posted_on:
        return None
    
    today = datetime.utcnow().date()
    posted_lower = posted_on.lower()
    
    if 'today' in posted_lower:
        return today.isoformat()
    elif 'yesterday' in posted_lower:
        return (today - timedelta(days=1)).isoformat()
    else:
        # Try to extract number of days
        match = re.search(r'(\d+)\+?\s*days?\s*ago', posted_lower)
        if match:
            days = int(match.group(1))
            return (today - timedelta(days=days)).isoformat()
    
    # Return as-is if we can't parse
    return posted_on


def fetch_workday(
    company: str,
    board_url: str,
    limit: int = 500,
    timeout: int = 30
) -> List[Dict]:
    """
    Fetch jobs from Workday hidden API.
    
    Args:
        company: Company name (for job records)
        board_url: Workday board URL (e.g., https://pnc.wd5.myworkdayjobs.com/External)
        limit: Maximum number of jobs to fetch
        timeout: Request timeout in seconds
    
    Returns:
        List of job dictionaries with normalized fields
    """
    api_url, base_url, site_id = parse_workday_url(board_url)
    
    if not api_url:
        print(f"[Workday] Invalid URL format for {company}: {board_url}")
        return []
    
    # Extract domain for headers
    parsed = urlparse(board_url)
    origin = f"https://{parsed.netloc}"
    
    # Create session and get cookies from main page first
    session = requests.Session()
    
    # Visit main page to get session cookies (critical for Workday)
    try:
        main_page = session.get(base_url, timeout=timeout)
        if main_page.status_code != 200:
            print(f"[Workday] Failed to get session for {company}: HTTP {main_page.status_code}")
            return []
    except requests.RequestException as e:
        print(f"[Workday] Session error for {company}: {e}")
        return []
    
    # Set headers for API calls (minimal set that works)
    session.headers.update({
        "Accept": "application/json",
        "Accept-Language": "en-US",
        "Content-Type": "application/json",
    })
    
    jobs = []
    offset = 0
    batch_size = 20  # Workday wd5/wd3 limit is 20; wd1/wd12 accept up to 50
    retries = 0
    max_retries = 3

    while offset < limit:
        payload = {
            "appliedFacets": {},
            "limit": batch_size,
            "offset": offset,
            "searchText": ""
        }

        try:
            r = session.post(api_url, json=payload, timeout=timeout)

            # Handle rate limiting (429)
            if r.status_code == 429:
                retries += 1
                if retries > max_retries:
                    print(f"[Workday] Max retries exceeded for {company}")
                    break
                wait_time = 2 * retries
                print(f"[Workday] Rate limited for {company}, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            
            if r.status_code != 200:
                # Try to understand the error
                error_text = r.text[:200] if r.text else "No response body"
                print(f"[Workday] HTTP {r.status_code} for {company}: {error_text}")
                break
            
            data = r.json()
            retries = 0  # Reset on success
            
        except requests.Timeout:
            print(f"[Workday] Timeout for {company}")
            break
        except requests.RequestException as e:
            print(f"[Workday] Request error for {company}: {e}")
            break
        except ValueError as e:
            print(f"[Workday] JSON parse error for {company}: {e}")
            break
        
        postings = data.get("jobPostings", [])
        total = data.get("total", 0)
        
        if not postings:
            break
        
        for p in postings:
            title = p.get("title", "")
            
            # Location: prefer locationsText, fallback to bulletFields
            loc_text = p.get("locationsText", "")
            if not loc_text or loc_text == "Multiple Locations":
                bullet_fields = p.get("bulletFields", [])
                # bulletFields often contains req ID first, then locations
                loc_candidates = [b for b in bullet_fields if not b.startswith("R") and not b.startswith("Posting")]
                if loc_candidates:
                    loc_text = ", ".join(loc_candidates)
            
            # Build job URL from externalPath
            external_path = p.get("externalPath", "")
            if external_path:
                job_url = f"{base_url}{external_path}"
            else:
                job_url = base_url
            
            # Parse posted date
            posted_on = p.get("postedOn", "")
            updated_at = parse_posted_on(posted_on)
            
            # Extract job requisition ID from bulletFields or path
            req_id = ""
            bullet_fields = p.get("bulletFields", [])
            for bf in bullet_fields:
                if bf.startswith("R") or bf.startswith("r") or re.match(r'^\d+$', bf):
                    req_id = bf
                    break
            if not req_id and external_path:
                # Try to extract from path like /job/.../Senior-Engineer_R227989-2
                match = re.search(r'[_-](R?\d+)(?:-\d+)?$', external_path)
                if match:
                    req_id = match.group(1)
            
            jobs.append({
                "company": company,
                "title": title,
                "location": loc_text,
                "job_url": job_url,
                "url": job_url,  # Alias for compatibility
                "updated_at": updated_at,
                "posted_on_raw": posted_on,  # Keep original for debugging
                "ats": "workday",
                "ats_job_id": req_id,
                "time_type": p.get("timeType", ""),
            })
        
        offset += batch_size
        
        # Stop if we've fetched all available jobs
        if offset >= total:
            break
        
        # Small delay between requests to be nice
        time.sleep(0.3)
    
    return jobs


# ============================================
# Known Workday companies - Banking & Fintech
# ============================================
# These are pre-configured URLs for quick reference.
# The actual source of truth is data/companies.json

KNOWN_WORKDAY_COMPANIES = {
    # Major Banks
    "Capital One": "https://capitalone.wd12.myworkdayjobs.com/Capital_One",
    "PNC": "https://pnc.wd5.myworkdayjobs.com/External",
    "Wells Fargo": "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs",
    "Bank of America": "https://ghr.wd1.myworkdayjobs.com/Lateral-US",
    "Truist": "https://truist.wd1.myworkdayjobs.com/Careers",
    
    # Investment / Wealth
    "Morgan Stanley": "https://ms.wd5.myworkdayjobs.com/External",
    "T. Rowe Price": "https://troweprice.wd5.myworkdayjobs.com/TROWEPRICE",
    "Capital Group": "https://capgroup.wd1.myworkdayjobs.com/capitalgroupcareers",
    "Raymond James": "https://raymondjames.wd1.myworkdayjobs.com/RaymondJamesCareers",
    
    # Fintech / Payments
    "Mastercard": "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers",
    
    # Tech
    "Workday": "https://workday.wd5.myworkdayjobs.com/Workday",
    "Salesforce": "https://salesforce.wd12.myworkdayjobs.com/External_Career_Site",
    "Adobe": "https://adobe.wd5.myworkdayjobs.com/external_experienced",
    "Nvidia": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
    "Cisco": "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers",
    "Zoom": "https://zoom.wd5.myworkdayjobs.com/Zoom",
}


def get_known_companies() -> Dict[str, str]:
    """Return known Workday companies with their board URLs."""
    return KNOWN_WORKDAY_COMPANIES.copy()


if __name__ == "__main__":
    import json
    
    print("Testing Workday parser with a single company...\n")
    
    # Test one company at a time to avoid rate limiting
    name = "Capital One"
    url = "https://capitalone.wd12.myworkdayjobs.com/Capital_One"
    
    print(f"Testing: {name}")
    print(f"URL: {url}")
    
    # Parse URL components
    api_url, base_url, site_id = parse_workday_url(url)
    print(f"API URL: {api_url}")
    print(f"Base URL: {base_url}")
    print(f"Site ID: {site_id}")
    
    # Fetch jobs
    jobs = fetch_workday(name, url, limit=10)
    print(f"\nFetched: {len(jobs)} jobs")
    
    if jobs:
        print("\nSample jobs:")
        for i, job in enumerate(jobs[:3]):
            print(f"  {i+1}. {job['title'][:60]}")
            print(f"     Location: {job['location']}")
            print(f"     Posted: {job['updated_at']}")
            print(f"     URL: {job['job_url'][:80]}...")
