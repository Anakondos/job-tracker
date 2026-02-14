# parsers/jibe.py
"""
Jibe (iCIMS) ATS Parser.

Jibe sits as a frontend over iCIMS and provides a clean JSON API.
Supports two URL patterns:
  - Standard: https://{client}.jibeapply.com/api/jobs
  - Custom domain: https://{custom-domain}/api/jobs (e.g. jobs.zs.com, careers.icims.com)

Response format:
{
  "jobs": [{"data": {"slug": "...", "title": "...", "city": "...", ...}}],
  "totalCount": N
}
"""

import requests
import re
from typing import List, Dict, Optional
from urllib.parse import urlparse


def _get_api_base(url: str) -> Optional[str]:
    """
    Build Jibe API base URL from board URL.

    Supports:
      https://firstcitizens.jibeapply.com/jobs  -> https://firstcitizens.jibeapply.com/api/jobs
      https://jobs.zs.com                       -> https://jobs.zs.com/api/jobs
      https://careers.icims.com                 -> https://careers.icims.com/api/jobs
    """
    parsed = urlparse(url)
    if not parsed.netloc:
        return None
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}/api/jobs"


def _get_jobs_base_url(url: str) -> str:
    """Get base URL for constructing job links."""
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    return f"{scheme}://{parsed.netloc}"


def fetch_jibe(company: str, board_url: str, limit: int = 2000, timeout: int = 30) -> List[Dict]:
    """
    Fetch jobs from Jibe JSON API.

    Args:
        company: Company name (for job records)
        board_url: Jibe URL, e.g. https://firstcitizens.jibeapply.com/jobs
                   or custom domain like https://jobs.zs.com
        limit: Maximum number of jobs to fetch
        timeout: Request timeout in seconds

    Returns:
        List of job dicts with normalized fields
    """
    api_base = _get_api_base(board_url)
    if not api_base:
        print(f"[Jibe] Cannot build API URL from: {board_url}")
        return []

    base_url = _get_jobs_base_url(board_url)
    jobs = []
    page = 1
    page_size = 100

    while len(jobs) < limit:
        try:
            r = requests.get(
                api_base,
                params={"page": page, "limit": page_size},
                timeout=timeout,
                headers={"Accept": "application/json"},
            )

            if r.status_code != 200:
                print(f"[Jibe] HTTP {r.status_code} for {company}")
                break

            data = r.json()
        except requests.RequestException as e:
            print(f"[Jibe] Request error for {company}: {e}")
            break
        except ValueError as e:
            print(f"[Jibe] JSON parse error for {company}: {e}")
            break

        postings = data.get("jobs", [])
        total = data.get("totalCount", 0)

        if not postings:
            break

        for p in postings:
            d = p.get("data", {})

            title = d.get("title", "")
            slug = d.get("slug", "")

            # Location
            full_location = d.get("full_location", "")
            if not full_location:
                city = d.get("city", "")
                state = d.get("state", "")
                parts = [x for x in [city, state] if x]
                full_location = ", ".join(parts)

            # Job URL — use apply_url or construct from slug
            apply_url = d.get("apply_url", "")
            if not apply_url and slug:
                apply_url = f"{base_url}/jobs/{slug}"

            # Dates
            posted_date = d.get("posted_date", "")
            update_date = d.get("update_date", "")
            updated_at = update_date or posted_date

            # Requisition ID
            req_id = d.get("req_id", "") or slug

            # Categories / department
            categories = d.get("categories", [])
            department = ""
            if categories:
                department = categories[0].get("name", "")

            jobs.append({
                "company": company,
                "title": title,
                "location": full_location,
                "url": apply_url,
                "updated_at": updated_at,
                "first_published": posted_date,
                "ats": "jibe",
                "ats_job_id": req_id,
                "department": department,
            })

        page += 1

        # Stop if we've fetched all available jobs
        if len(jobs) >= total:
            break

    return jobs[:limit]
