# parsers/smartrecruiters.py

import requests
from urllib.parse import urljoin


def fetch_smartrecruiters(company: str, api_url: str, base_url: str | None = None):
    """
    company  – имя компании для отображения
    api_url  – SmartRecruiters endpoint:
               https://api.smartrecruiters.com/v1/companies/<slug>/postings
    base_url – сайт вакансий, например:
               https://careers.smartrecruiters.com/Atlassian
    """

    params = {"limit": 100, "offset": 0}
    jobs = []

    while True:
        r = requests.get(api_url, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()

        postings = data.get("content") or []
        if not postings:
            break

        for p in postings:
            title = p.get("name")

            loc_info = p.get("location") or {}
            city = loc_info.get("city") or ""
            region = loc_info.get("region") or ""
            country = loc_info.get("country") or ""
            loc_parts = [x for x in [city, region, country] if x]
            loc_str = ", ".join(loc_parts)

            ref = p.get("ref") or ""
            if base_url and ref and not ref.startswith("http"):
                job_url = urljoin(base_url.rstrip("/") + "/", ref.lstrip("/"))
            else:
                job_url = ref or base_url or api_url

            updated_at = p.get("releasedDate") or p.get("createdOn")

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
