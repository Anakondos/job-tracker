# parsers/lever.py

import requests


def fetch_lever(company: str, base_url: str):
    """
    base_url: https://jobs.lever.co/airbnb
    API:      https://api.lever.co/v0/postings/airbnb?mode=json
    """
    slug = base_url.rstrip("/").split("/")[-1]
    api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"

    r = requests.get(api_url, timeout=20)
    r.raise_for_status()
    data = r.json()

    jobs = []
    for job in data:
        categories = job.get("categories") or {}
        location = categories.get("location") or ""
        dept = categories.get("team") or ""

        jobs.append(
            {
                "company": company,
                "ats": "lever",
                "ats_job_id": job.get("id", ""),
                "title": job.get("text"),
                "location": location,
                "department": dept,
                "url": job.get("hostedUrl"),
                "first_published": job.get("createdAt"),
                "updated_at": job.get("createdAt"),  # Lever uses createdAt
            }
        )

    return jobs
