# parsers/smartrecruiters.py

import re
import requests
from urllib.parse import urljoin


def _normalize_sr_url(url: str) -> str:
    """
    Конвертируем любой SmartRecruiters URL в API URL.
    jobs.smartrecruiters.com/BoschGroup → api.smartrecruiters.com/v1/companies/BoschGroup/postings
    Если уже API URL — возвращаем как есть.
    """
    if "api.smartrecruiters.com" in url:
        return url
    # jobs.smartrecruiters.com/{slug} или careers.smartrecruiters.com/{slug}
    m = re.search(r"smartrecruiters\.com/([^/?#]+)", url)
    if m:
        slug = m.group(1)
        return f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    return url


def fetch_smartrecruiters(company: str, api_url: str, base_url: str = None):
    """
    company  – имя компании для отображения
    api_url  – SmartRecruiters endpoint (любой формат):
               https://api.smartrecruiters.com/v1/companies/<slug>/postings
               https://jobs.smartrecruiters.com/<slug>
    base_url – сайт вакансий, например:
               https://careers.smartrecruiters.com/Atlassian
    """
    api_url = _normalize_sr_url(api_url)

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
                    "ats": "smartrecruiters",
                    "ats_job_id": p.get("id") or p.get("uuid") or "",
                    "title": title,
                    "location": loc_str,
                    "department": p.get("department") or "",
                    "url": job_url,
                    "first_published": p.get("releasedDate") or p.get("createdOn"),
                    "updated_at": updated_at,
                }
            )

        if len(postings) < params["limit"]:
            break
        params["offset"] += params["limit"]

    return jobs
