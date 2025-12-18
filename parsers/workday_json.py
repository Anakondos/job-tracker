import requests
from urllib.parse import urlparse


def fetch_workday_json(company: str, api_url: str):
    """
    Универсальный парсер Workday JSON-эндпоинта /wday/cxs/.../jobs.

    Делаем POST с заголовками, похожими на браузер,
    и вытаскиваем title / location / externalUrl / postedOn.
    """

    parsed = urlparse(api_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": origin,
        "Referer": origin,
        # просто нормальный UA, чтобы не палиться как скрипт
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    payload = {
        "appliedFacets": {},
        "limit": 50,
        "offset": 0,
        "searchText": "",
    }

    resp = requests.post(api_url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    jobs: list[dict] = []

    # у Workday обычно либо jobPostings, либо items
    postings = []
    if isinstance(data, dict):
        if isinstance(data.get("jobPostings"), list):
            postings = data["jobPostings"]
        elif isinstance(data.get("items"), list):
            postings = data["items"]
    elif isinstance(data, list):
        postings = data

    for item in postings:
        if not isinstance(item, dict):
            continue

        title = (
            item.get("title")
            or item.get("externalJobTitle")
            or item.get("jobPostingTitle")
        )

        raw_location = (
            item.get("locationsText")
            or item.get("location")
            or item.get("city")
        )
        location = str(raw_location) if raw_location is not None else ""

        url = (
            item.get("externalUrl")
            or item.get("url")
            or item.get("jobPostingURL")
        )

        updated = (
            item.get("postedOn")
            or item.get("postingDate")
            or item.get("creationDate")
        )

        if not title or not url:
            continue

        # иногда Workday отдаёт относительные URL — подстрахуемся
        if isinstance(url, str) and url.startswith("/"):
            full_url = origin + url
        else:
            full_url = url

        jobs.append(
            {
                "company": company,
                "title": title,
                "location": location,
                "url": full_url,
                "updated_at": updated,
            }
        )

    return jobs

