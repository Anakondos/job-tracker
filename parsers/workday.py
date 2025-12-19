# parsers/workday.py

import requests
from urllib.parse import urljoin


def fetch_workday(company: str, api_url: str, base_url: str | None = None):
    """
    company   – логическое имя компании
    api_url   – Workday JSON endpoint:
                https://.../wday/cxs/.../jobs
    base_url  – карьерный сайт (для формирования ссылок):
                https://.../en-US/...
    """

    params = {"limit": 50, "offset": 0}
    jobs = []

    while True:
        r = requests.get(api_url, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()

        postings = data.get("jobPostings") or data.get("value") or []
        if not postings:
            break

        for p in postings:
            title = p.get("title")

            locations = p.get("locationsText") or p.get("locations") or []
            if isinstance(locations, list):
                loc_str = ", ".join(locations)
            else:
                loc_str = str(locations) if locations else ""

            external_path = p.get("externalPath") or p.get("externalUrl") or ""
            if base_url and external_path:
                job_url = urljoin(base_url.rstrip("/") + "/", external_path.lstrip("/"))
            elif external_path:
                job_url = urljoin(api_url, external_path)
            else:
                job_url = base_url or api_url

            updated_at = (
                p.get("postedOn")
                or p.get("startDate")
                or p.get("lastUpdated")
                or p.get("postingPublishDate")
            )

            jobs.append(
                {
                    "company": company,
                    "title": title,
                    "location": loc_str,
                    "department": p.get("department") or "",
                    "url": job_url,
                    "updated_at": updated_at,
                }
            )

        if len(postings) < params["limit"]:
            break
        params["offset"] += params["limit"]

    return jobs

