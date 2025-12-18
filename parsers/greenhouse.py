# parsers/greenhouse.py

import requests


def fetch_greenhouse(company: str, base_url: str):
    """
    base_url: https://boards.greenhouse.io/brex
    API:      https://boards-api.greenhouse.io/v1/boards/brex/jobs
    """
    token = base_url.rstrip("/").split("/")[-1]
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"

    r = requests.get(api_url, timeout=20)
    r.raise_for_status()
    data = r.json()

    jobs = []
    for job in data.get("jobs", []):
        location = job.get("location", {}).get("name", "")
        departments = job.get("departments") or []
        dept = departments[0]["name"] if departments else ""

        jobs.append(
            {
                "company": company,
                "title": job.get("title"),
                "location": location,
                "department": dept,
                "url": job.get("absolute_url"),
                "updated_at": job.get("updated_at"),
            }
        )

    return jobs
