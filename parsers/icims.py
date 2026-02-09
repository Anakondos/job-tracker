# parsers/icims.py
"""
iCIMS ATS Parser — парсинг вакансий с iCIMS career pages.

iCIMS отдаёт HTML (не JSON API), поэтому парсим через BeautifulSoup.
На каждой странице ~50 вакансий, пагинация через параметр ?pr=N.

URL формат: https://{subdomain}.icims.com/jobs/search?ss=1&in_iframe=1&pr={page}

Некоторые iCIMS порталы также содержат встроенный JS-массив jobImpressions
с более чистыми данными — используем его если доступен.
"""

import json
import re
import requests
from typing import List, Dict


def fetch_icims(company: str, base_url: str) -> List[Dict]:
    """
    Парсим вакансии с iCIMS portal.

    Args:
        company: Название компании (для заполнения job dict)
        base_url: URL карьерной страницы, напр. https://careers-aptiveresources.icims.com/jobs

    Returns:
        Список вакансий в формате RawJob (schema.py)
    """
    # Нормализуем base_url
    base = base_url.rstrip("/")
    if not base.endswith("/jobs"):
        base = base.rstrip("/") + "/jobs"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    all_jobs = []
    page = 0
    max_pages = 20  # safety limit

    while page < max_pages:
        url = f"{base}/search?ss=1&in_iframe=1&pr={page}"

        try:
            r = requests.get(url, timeout=20, headers=headers)
            r.raise_for_status()
        except Exception as e:
            if page == 0:
                raise  # Первая страница обязательна
            break

        html = r.text

        # Пробуем встроенный JS-массив jobImpressions (более надёжный)
        js_jobs = _parse_job_impressions(html, base)
        if js_jobs:
            all_jobs.extend(js_jobs)
        else:
            # Fallback на HTML-парсинг через BeautifulSoup
            html_jobs = _parse_html_jobs(html, base)
            if not html_jobs:
                break  # Пустая страница — конец пагинации
            all_jobs.extend(html_jobs)

        # Проверяем есть ли следующая страница
        if not _has_next_page(html, page):
            break

        page += 1

    # Дедупликация по ats_job_id
    seen = set()
    unique_jobs = []
    for job in all_jobs:
        jid = job.get("ats_job_id", "")
        if jid and jid in seen:
            continue
        seen.add(jid)
        # Проставляем company
        job["company"] = company
        job["ats"] = "icims"
        unique_jobs.append(job)

    return unique_jobs


def _parse_job_impressions(html: str, base_url: str) -> List[Dict]:
    """
    Парсим встроенный JS-массив jobImpressions если он есть.
    Формат: var jobImpressions = [{...}, {...}, ...];
    """
    match = re.search(r"var\s+jobImpressions\s*=\s*(\[.+?\]);", html, re.DOTALL)
    if not match:
        return []

    try:
        impressions = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    jobs = []
    for imp in impressions:
        job_id = str(imp.get("idRaw", ""))
        title = imp.get("title", "")

        # Собираем location из вложенного объекта
        loc = imp.get("location", {})
        if isinstance(loc, dict):
            parts = [loc.get("city", ""), loc.get("state", "")]
            location = ", ".join(p for p in parts if p and p != "not set")
        else:
            location = str(loc) if loc else ""

        # URL вакансии
        job_url = f"{base_url}/{job_id}/job" if job_id else ""

        jobs.append({
            "ats_job_id": job_id,
            "title": title,
            "location": location,
            "department": imp.get("category", ""),
            "url": job_url,
            "first_published": imp.get("postedDate", ""),
            "updated_at": imp.get("postedDate", ""),
        })

    return jobs


def _parse_html_jobs(html: str, base_url: str) -> List[Dict]:
    """
    Fallback HTML парсинг через BeautifulSoup.
    Ищем .iCIMS_JobsTable контейнер с рядами вакансий.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[iCIMS] bs4 not installed, cannot parse HTML. Run: pip install beautifulsoup4")
        return []

    soup = BeautifulSoup(html, "html.parser")
    jobs = []

    # Ищем все ссылки на вакансии (класс iCIMS_Anchor)
    for anchor in soup.select("a.iCIMS_Anchor"):
        href = anchor.get("href", "")
        title_attr = anchor.get("title", "")

        # title формат: "4129 - Neurosurgeon"
        job_id = ""
        # Берём title из <h3> внутри anchor (избегаем sr-only "Title" prefix)
        h3 = anchor.select_one("h3")
        title = h3.get_text(strip=True) if h3 else ""

        if title_attr and " - " in title_attr:
            parts = title_attr.split(" - ", 1)
            job_id = parts[0].strip()
            if not title:
                title = parts[1].strip()

        if not title:
            continue

        # URL — может быть относительный
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            job_url = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif href.startswith("http"):
            job_url = href
        else:
            job_url = ""

        # Убираем in_iframe из URL
        job_url = re.sub(r"[?&]in_iframe=1", "", job_url)
        job_url = re.sub(r"\?$", "", job_url)

        # Ищем location в соседних элементах
        location = ""
        row = anchor.find_parent("div", class_="row")
        if row:
            loc_span = row.select_one(".header.left span:not(.sr-only)")
            if loc_span:
                location = loc_span.get_text(strip=True)
                # Формат "US-WA-Seattle" → "Seattle, WA"
                location = _normalize_icims_location(location)

        # Ищем department/category
        department = ""
        if row:
            for tag in row.select(".iCIMS_JobHeaderTag"):
                field = tag.select_one(".iCIMS_JobHeaderField")
                data = tag.select_one(".iCIMS_JobHeaderData span")
                if field and data and "category" in field.get_text(strip=True).lower():
                    department = data.get_text(strip=True)

        # Ищем ID из additionalFields если не нашли из title
        if not job_id and row:
            for tag in row.select(".iCIMS_JobHeaderTag"):
                field = tag.select_one(".iCIMS_JobHeaderField")
                data = tag.select_one(".iCIMS_JobHeaderData span")
                if field and data and field.get_text(strip=True).upper() == "ID":
                    raw_id = data.get_text(strip=True)
                    # Формат "2026-4129" → "4129"
                    job_id = raw_id.split("-")[-1] if "-" in raw_id else raw_id

        # Если job_id не нашли — извлекаем из URL: /jobs/4129/title/job
        if not job_id and "/jobs/" in job_url:
            id_match = re.search(r"/jobs/(\d+)", job_url)
            if id_match:
                job_id = id_match.group(1)

        jobs.append({
            "ats_job_id": job_id,
            "title": title,
            "location": location,
            "department": department,
            "url": job_url,
            "first_published": "",
            "updated_at": "",
        })

    return jobs


def _normalize_icims_location(raw: str) -> str:
    """
    Нормализуем iCIMS location формат.
    "US-WA-Seattle" → "Seattle, WA"
    "US-Remote" → "Remote, US"
    """
    if not raw:
        return ""

    parts = raw.split("-")
    if len(parts) == 3 and parts[0] == "US":
        return f"{parts[2]}, {parts[1]}"
    if len(parts) == 2 and parts[0] == "US":
        return f"{parts[1]}, US"
    return raw


def _has_next_page(html: str, current_page: int) -> bool:
    """Проверяем есть ли следующая страница в пагинации."""
    # Ищем ссылку на следующую страницу
    next_page = current_page + 1
    return f"pr={next_page}" in html


if __name__ == "__main__":
    # Тест
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://careers-aptiveresources.icims.com/jobs"
    print(f"Testing iCIMS parser: {test_url}\n")

    jobs = fetch_icims("TestCompany", test_url)
    print(f"Found {len(jobs)} jobs\n")

    for job in jobs[:10]:
        print(f"  [{job.get('ats_job_id')}] {job.get('title')}")
        print(f"    Location: {job.get('location')}")
        print(f"    URL: {job.get('url')}")
        print()
