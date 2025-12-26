# parsers/atlassian.py

"""
Atlassian-specific parser using their public Phenom People API.

API: https://join.atlassian.com/api/jobs
- Pagination: ?limit=100&offset=0 (max 100 per request)
- Returns JSON with jobs array, each job has 'data' wrapper
- Total jobs available in 'totalCount' field

Note: This is NOT a generic iCIMS or Phenom parser - it's specific
to Atlassian's public career site implementation.
"""

import requests


def fetch_atlassian(company: str, base_url: str = None):
    """
    Fetch all jobs from Atlassian's career API.
    
    Args:
        company: Company name (should be "Atlassian")
        base_url: Ignored, API URL is hardcoded
        
    Returns:
        List of normalized job dicts
    """
    api_url = "https://join.atlassian.com/api/jobs"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://www.atlassian.com",
        "Referer": "https://www.atlassian.com/"
    }
    
    all_jobs = []
    limit = 100
    offset = 0
    
    # First request to get total count
    r = requests.get(f"{api_url}?limit={limit}&offset={offset}", headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    
    total_count = data.get("totalCount", 0)
    all_jobs.extend(data.get("jobs", []))
    
    # Fetch remaining pages
    while offset + limit < total_count:
        offset += limit
        r = requests.get(f"{api_url}?limit={limit}&offset={offset}", headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        all_jobs.extend(data.get("jobs", []))
    
    # Normalize jobs
    jobs = []
    for job_wrapper in all_jobs:
        job = job_wrapper.get("data", {})
        
        # Build location string
        location_parts = []
        if job.get("city"):
            location_parts.append(job["city"])
        if job.get("country"):
            location_parts.append(job["country"])
        location = ", ".join(location_parts) if location_parts else job.get("location_name", "")
        
        # Get category/department
        categories = job.get("category", [])
        department = categories[0].strip() if categories else ""
        
        # Build job URL - use apply_url or construct from req_id
        job_url = job.get("apply_url") or f"https://www.atlassian.com/company/careers/details/{job.get('req_id', '')}"
        
        jobs.append({
            "company": company,
            "ats": "atlassian",
            "ats_job_id": str(job.get("req_id", "")),
            "title": job.get("title", ""),
            "location": location,
            "department": department,
            "url": job_url,
            "first_published": job.get("posted_date"),
            "updated_at": job.get("update_date"),
        })
    
    return jobs


if __name__ == "__main__":
    # Quick test
    jobs = fetch_atlassian("Atlassian")
    print(f"Fetched {len(jobs)} jobs from Atlassian")
    if jobs:
        print(f"\nSample job:")
        for k, v in jobs[0].items():
            print(f"  {k}: {v}")
