# parsers/greenhouse.py

import time
import requests


def fetch_greenhouse(company: str, base_url: str):
    """
    base_url: https://boards.greenhouse.io/brex
    API:      https://boards-api.greenhouse.io/v1/boards/brex/jobs
    """
    token = base_url.rstrip("/").split("/")[-1]
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    for attempt in range(3):
        try:
            r = requests.get(api_url, timeout=30)
            r.raise_for_status()
            data = r.json()
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            if attempt < 2:
                print(f"[Greenhouse] Retry {attempt+1} for {token}: {e}")
                time.sleep(2)
            else:
                print(f"[Greenhouse] Failed after 3 retries for {token}: {e}")
                raise

    jobs = []
    for job in data.get("jobs", []):
        location = job.get("location", {}).get("name", "")
        departments = job.get("departments") or []
        dept = departments[0]["name"] if departments else ""

        jobs.append(
            {
                "company": company,
                "ats": "greenhouse",
                "ats_job_id": str(job.get("id", "")),
                "title": job.get("title"),
                "location": location,
                "department": dept,
                "url": job.get("absolute_url"),
                "first_published": job.get("first_published"),
                "updated_at": job.get("updated_at"),
            }
        )

    return jobs
