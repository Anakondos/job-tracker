"""
Ashby ATS Parser
API: https://jobs.ashbyhq.com/{company}
"""
import requests

def fetch_ashby_jobs(board_url: str) -> list[dict]:
    """
    Fetches jobs from Ashby API.
    board_url example: https://jobs.ashbyhq.com/notion
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
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "department": job.get("department", ""),
            "url": job.get("jobUrl", ""),
            "updated_at": job.get("publishedAt", ""),
            "ats_job_id": job.get("id", ""),
        })
    
    return jobs


if __name__ == "__main__":
    # Test
    jobs = fetch_ashby_jobs("https://jobs.ashbyhq.com/notion")
    print(f"Found {len(jobs)} jobs at Notion")
    for j in jobs[:3]:
        print(f"  - {j['title']} @ {j['location']}")
