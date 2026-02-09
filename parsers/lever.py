# parsers/lever.py

import requests
from datetime import datetime, timezone


def _ms_to_iso(ms_timestamp) -> str:
    """Convert millisecond timestamp to ISO string."""
    if isinstance(ms_timestamp, (int, float)) and ms_timestamp > 0:
        ts = ms_timestamp / 1000 if ms_timestamp > 1e12 else ms_timestamp
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (OSError, ValueError):
            pass
    return ""


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
        created_iso = _ms_to_iso(job.get("createdAt"))

        jobs.append(
            {
                "company": company,
                "ats": "lever",
                "ats_job_id": job.get("id", ""),
                "title": job.get("text"),
                "location": location,
                "department": dept,
                "url": job.get("hostedUrl"),
                "first_published": created_iso,
                "updated_at": created_iso,
            }
        )

    return jobs
