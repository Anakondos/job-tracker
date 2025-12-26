"""
Ashby ATS Parser
API: https://jobs.ashbyhq.com/{company}

Returns: List[RawJob] per schema.py contract
"""
import requests

def fetch_ashby_jobs(board_url: str) -> list[dict]:
    """
    Fetches jobs from Ashby API.
    board_url example: https://jobs.ashbyhq.com/notion
    
    Returns RawJob list with required fields:
    - title, url, ats_job_id, location, updated_at
    """
    # Extract company slug
    company_slug = board_url.rstrip("/").split("/")[-1]
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0)"}
    resp = requests.get(api_url, headers=headers, timeout=15)
    resp.raise_for_status()
    
    data = resp.json()
    jobs = []
    
    for job in data.get("jobs", []):
        jobs.append({
            # Required
            "title": job.get("title", ""),
            "url": job.get("jobUrl", ""),
            # Expected  
            "ats_job_id": job.get("id", ""),
            "location": job.get("location", ""),
            "updated_at": job.get("publishedAt", ""),
            # Optional
            "department": job.get("department", ""),
            "first_published": job.get("publishedAt", ""),
        })
    
    return jobs


if __name__ == "__main__":
    # Test
    jobs = fetch_ashby_jobs("https://jobs.ashbyhq.com/notion")
    print(f"Found {len(jobs)} jobs at Notion")
    for j in jobs[:3]:
        print(f"  - {j['title']} @ {j['location']}")
