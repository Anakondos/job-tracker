# parsers/jibe.py
"""
Jibe (Google Hire) ATS Parser.

Jibe sits as a frontend over iCIMS and provides a clean JSON API.
API endpoint: https://{client}.jibeapply.com/api/jobs?page={page}&limit={limit}

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


def _extract_jibe_client(url: str) -> Optional[str]:
    """
    Extract client code from Jibe URL.

    Examples:
      https://firstcitizens.jibeapply.com/jobs  -> firstcitizens
      https://firstcitizens.jibeapply.com       -> firstcitizens
    """
    parsed = urlparse(url)
    host = parsed.netloc or ""
    match = re.match(r'^([^.]+)\.jibeapply\.com$', host)
    if match:
        return match.group(1)
    return None


def fetch_jibe(company: str, board_url: str, limit: int = 2000, timeout: int = 30) -> List[Dict]:
    """
    Fetch jobs from Jibe JSON API.

    Args:
        company: Company name (for job records)
        board_url: Jibe URL, e.g. https://firstcitizens.jibeapply.com/jobs
        limit: Maximum number of jobs to fetch
        timeout: Request timeout in seconds

    Returns:
        List of job dicts with normalized fields
    """
    client = _extract_jibe_client(board_url)
    if not client:
        print(f"[Jibe] Cannot extract client from URL: {board_url}")
        return []

    api_base = f"https://{client}.jibeapply.com/api/jobs"
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
                apply_url = f"https://{client}.jibeapply.com/jobs/{slug}"

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
