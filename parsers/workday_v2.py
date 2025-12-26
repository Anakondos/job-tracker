# parsers/workday_v2.py
"""
Workday Career Site Parser v2
Использует скрытый JSON API myworkdayjobs.com для получения вакансий.

URL pattern: https://{company}.{wd_instance}.myworkdayjobs.com/{board_name}
API endpoint: https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{board_name}/jobs
"""

import requests
from urllib.parse import urlparse
from typing import Optional
from datetime import datetime, timedelta
import re
import logging

logger = logging.getLogger(__name__)


def parse_workday_url(board_url: str) -> dict:
    """
    Парсит URL карьерной страницы Workday и извлекает компоненты.
    """
    parsed = urlparse(board_url)
    hostname = parsed.netloc

    host_match = re.match(r'^([^.]+)\.(wd\d+)\.myworkdayjobs\.com$', hostname)
    if not host_match:
        raise ValueError(f"Invalid Workday URL format: {board_url}")

    company = host_match.group(1)
    wd_instance = host_match.group(2)

    path_parts = [p for p in parsed.path.split('/') if p]

    board_name = None
    for part in path_parts:
        if not re.match(r'^[a-z]{2}-[A-Z]{2}$', part):
            board_name = part
            break

    if not board_name:
        raise ValueError(f"Cannot extract board_name from URL: {board_url}")

    api_url = f"https://{hostname}/wday/cxs/{company}/{board_name}/jobs"
    base_url = f"https://{hostname}/en-US/{board_name}"

    return {
        "company": company,
        "wd_instance": wd_instance,
        "board_name": board_name,
        "api_url": api_url,
        "base_url": base_url,
        "hostname": hostname
    }


def _parse_posted_on(posted_on: str) -> Optional[str]:
    """
    Конвертирует "Posted Yesterday", "Posted 3 Days Ago" и т.д. в ISO дату.
    """
    if not posted_on:
        return None
    
    now = datetime.utcnow()
    posted_lower = posted_on.lower()
    
    if "today" in posted_lower:
        dt = now
    elif "yesterday" in posted_lower:
        dt = now - timedelta(days=1)
    elif "30+ days" in posted_lower:
        dt = now - timedelta(days=35)
    else:
        # Try to extract number of days: "Posted 3 Days Ago"
        match = re.search(r'(\d+)\s*days?\s*ago', posted_lower)
        if match:
            days = int(match.group(1))
            dt = now - timedelta(days=days)
        else:
            return None
    
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_workday_v2(
    company: str,
    board_url: str,
    search_text: str = "",
    limit: int = 20,
    max_jobs: Optional[int] = None,
    applied_facets: Optional[dict] = None
) -> list[dict]:
    """
    Получает вакансии с Workday Career Site через скрытый JSON API.
    """
    try:
        url_parts = parse_workday_url(board_url)
    except ValueError as e:
        logger.error(f"Failed to parse Workday URL for {company}: {e}")
        return []

    api_url = url_parts["api_url"]
    base_url = url_parts["base_url"]
    hostname = url_parts["hostname"]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": f"https://{hostname}",
        "Referer": f"https://{hostname}/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    payload = {
        "appliedFacets": applied_facets or {},
        "limit": min(limit, 20),
        "offset": 0,
        "searchText": search_text,
    }

    all_jobs = []
    total_available = None

    while True:
        try:
            resp = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"Request failed for {company} at offset {payload['offset']}: {e}")
            break
        except ValueError as e:
            logger.error(f"JSON decode failed for {company}: {e}")
            break

        if total_available is None:
            total_available = data.get("total", 0)
            logger.info(f"[{company}] Total jobs available: {total_available}")

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for item in postings:
            job = _normalize_job(item, company, base_url)
            if job:
                all_jobs.append(job)

        if max_jobs and len(all_jobs) >= max_jobs:
            all_jobs = all_jobs[:max_jobs]
            break

        payload["offset"] += len(postings)
        if payload["offset"] >= total_available:
            break

    logger.info(f"[{company}] Fetched {len(all_jobs)} jobs")
    return all_jobs


def _normalize_job(item: dict, company: str, base_url: str) -> Optional[dict]:
    """
    Нормализует вакансию из Workday API в стандартный формат.
    """
    title = item.get("title")
    if not title:
        return None

    external_path = item.get("externalPath", "")
    if external_path:
        job_url = f"{base_url}{external_path}"
    else:
        job_url = base_url

    location = item.get("locationsText", "")
    
    # Posted date - convert to ISO format
    posted_on = item.get("postedOn", "")
    updated_at = _parse_posted_on(posted_on)

    bullet_fields = item.get("bulletFields", [])
    job_id = bullet_fields[0] if bullet_fields else ""

    time_type = item.get("timeType", "")

    return {
        "company": company,
        "ats": "workday",
        "ats_job_id": job_id,
        "title": title,
        "location": location,
        "url": job_url,
        "posted_on": posted_on,
        "time_type": time_type,
        "updated_at": updated_at,  # Now has actual date!
    }


def fetch_workday(company: str, board_url: str, **kwargs) -> list[dict]:
    """Wrapper для совместимости."""
    return fetch_workday_v2(company, board_url, **kwargs)


def fetch_workday_v2_streaming(
    company: str,
    board_url: str,
    search_text: str = "",
    limit: int = 20,
    max_jobs: Optional[int] = None,
    applied_facets: Optional[dict] = None
):
    """
    Generator версия - yields progress events.
    Yields: {"type": "progress", "jobs": count} или {"type": "done", "jobs": list}
    """
    try:
        url_parts = parse_workday_url(board_url)
    except ValueError as e:
        logger.error(f"Failed to parse Workday URL for {company}: {e}")
        yield {"type": "error", "error": str(e)}
        return

    api_url = url_parts["api_url"]
    base_url = url_parts["base_url"]
    hostname = url_parts["hostname"]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": f"https://{hostname}",
        "Referer": f"https://{hostname}/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    payload = {
        "appliedFacets": applied_facets or {},
        "limit": min(limit, 20),
        "offset": 0,
        "searchText": search_text,
    }

    all_jobs = []
    total_available = None

    while True:
        try:
            resp = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error(f"Request failed for {company} at offset {payload['offset']}: {e}")
            yield {"type": "error", "error": str(e)}
            return
        except ValueError as e:
            logger.error(f"JSON decode failed for {company}: {e}")
            yield {"type": "error", "error": str(e)}
            return

        if total_available is None:
            total_available = data.get("total", 0)
            logger.info(f"[{company}] Total jobs available: {total_available}")

        postings = data.get("jobPostings", [])
        if not postings:
            break

        for item in postings:
            job = _normalize_job(item, company, base_url)
            if job:
                all_jobs.append(job)

        # Yield progress after each page
        yield {"type": "progress", "jobs": len(all_jobs), "total": total_available}

        if max_jobs and len(all_jobs) >= max_jobs:
            all_jobs = all_jobs[:max_jobs]
            break

        payload["offset"] += len(postings)
        if payload["offset"] >= total_available:
            break

    logger.info(f"[{company}] Fetched {len(all_jobs)} jobs")
    yield {"type": "done", "jobs": all_jobs}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    jobs = fetch_workday_v2(
        company="Capital One",
        board_url="https://capitalone.wd12.myworkdayjobs.com/Capital_One",
        max_jobs=5
    )
    
    print(f"\n=== Fetched {len(jobs)} jobs ===\n")
    for job in jobs:
        print(f"- {job['title']}")
        print(f"  Location: {job['location']}")
        print(f"  Posted: {job['posted_on']}")
        print(f"  updated_at: {job['updated_at']}")
        print()
