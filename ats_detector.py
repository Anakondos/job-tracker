"""
ATS Detector - автоматическое обнаружение и ремонт ATS URL компаний

Использование:
1. detect_ats(careers_url) - определить ATS по careers странице
2. try_repair_company(company_data) - попытаться найти рабочий URL для компании с ошибкой
3. repair_all_broken() - починить все компании с ошибками в companies.json
"""
import re
import json
import requests
from pathlib import Path
from urllib.parse import urlparse

ATS_PATTERNS = {
    "greenhouse": [
        r"boards\.greenhouse\.io/([a-zA-Z0-9_-]+)",
        r"boards-api\.greenhouse\.io/v1/boards/([a-zA-Z0-9_-]+)",
        r"greenhouse\.io.*?/([a-zA-Z0-9_-]+)/jobs",
    ],
    "lever": [
        r"jobs\.lever\.co/([a-zA-Z0-9_-]+)",
        r"api\.lever\.co/v0/postings/([a-zA-Z0-9_-]+)",
    ],
    "smartrecruiters": [
        r"jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
        r"careers\.smartrecruiters\.com/([a-zA-Z0-9_-]+)",
    ],
    "ashby": [
        r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)",
        r"api\.ashbyhq\.com/posting-api/job-board/([a-zA-Z0-9_-]+)",
    ],
    "workday": [
        r"([a-zA-Z0-9_-]+)\.wd\d+\.myworkdayjobs\.com",
    ],
}

# Стандартные careers URL паттерны
CAREERS_URL_PATTERNS = [
    "{domain}/careers",
    "{domain}/jobs",
    "{domain}/about/careers",
    "{domain}/company/careers",
    "careers.{domain}",
    "jobs.{domain}",
]


def detect_ats(careers_url: str) -> dict | None:
    """
    Загружает careers страницу и ищет ссылки на ATS.
    Возвращает {"ats": "greenhouse", "board_id": "openai", "board_url": "..."} или None
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        resp = requests.get(careers_url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        
        # Проверяем и финальный URL после редиректов
        final_url = resp.url
        texts_to_check = [html, final_url]
        
        for ats_name, patterns in ATS_PATTERNS.items():
            for pattern in patterns:
                for text in texts_to_check:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        board_id = match.group(1).lower()
                        board_url = build_board_url(ats_name, board_id)
                        api_url = build_api_url(ats_name, board_id)
                        
                        # Verify using API URL
                        if verify_ats_url(api_url):
                            return {
                                "ats": ats_name,
                                "board_id": board_id,
                                "board_url": board_url,
                                "source": careers_url,
                                "verified": True
                            }
        
        return None
        
    except Exception as e:
        return {"error": str(e)}


def build_board_url(ats: str, board_id: str) -> str:
    """Builds the board URL for storing in companies.json (used by parsers)"""
    urls = {
        "greenhouse": f"https://boards.greenhouse.io/{board_id}",
        "lever": f"https://jobs.lever.co/{board_id}",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{board_id}",
        "ashby": f"https://jobs.ashbyhq.com/{board_id}",
        "workday": "",
    }
    return urls.get(ats, "")


def build_api_url(ats: str, board_id: str) -> str:
    """Builds the API URL for verification"""
    urls = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs",
        "lever": f"https://api.lever.co/v0/postings/{board_id}?mode=json",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{board_id}/postings",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{board_id}",
        "workday": "",
    }
    return urls.get(ats, "")


def verify_ats_url(board_url: str) -> bool:
    """Проверяет что ATS URL работает и возвращает данные"""
    if not board_url:
        return False
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0)"}
        resp = requests.get(board_url, headers=headers, timeout=10)
        return resp.status_code == 200
    except:
        return False


def guess_careers_urls(company_name: str, website: str = None) -> list:
    """Генерирует возможные careers URL для компании"""
    urls = []
    
    # Если есть website
    if website:
        domain = website.replace("https://", "").replace("http://", "").rstrip("/")
        for pattern in CAREERS_URL_PATTERNS:
            url = "https://" + pattern.format(domain=domain)
            urls.append(url)
    
    # Пробуем угадать по имени компании
    slug = company_name.lower().replace(" ", "").replace("-", "").replace(".", "")
    common_domains = [
        f"https://{slug}.com/careers",
        f"https://www.{slug}.com/careers",
        f"https://{slug}.io/careers",
    ]
    urls.extend(common_domains)
    
    return urls


def try_repair_company(company: dict) -> dict | None:
    """
    Пытается найти рабочий ATS URL для компании.
    
    Args:
        company: dict с полями name, ats, board_url, и опционально website, careers_url
    
    Returns:
        dict с новыми ats, board_url если нашли, или None
    """
    company_name = company.get("name", "")
    print(f"🔧 Trying to repair: {company_name}")
    
    # 1. Если есть careers_url - пробуем его
    if company.get("careers_url"):
        result = detect_ats(company["careers_url"])
        if result and result.get("verified"):
            print(f"  ✅ Found via careers_url: {result['ats']}")
            return result
    
    # 2. Пробуем угадать careers URL
    guess_urls = guess_careers_urls(company_name, company.get("website"))
    
    for url in guess_urls:
        result = detect_ats(url)
        if result and result.get("verified"):
            print(f"  ✅ Found via {url}: {result['ats']}")
            return result
    
    # 3. Пробуем напрямую известные ATS с slug компании
    slug = company_name.lower().replace(" ", "").replace("-", "").replace(".", "")
    
    direct_urls = [
        ("greenhouse", slug),
        ("lever", slug),
        ("ashby", slug),
    ]
    
    for ats, board_id in direct_urls:
        api_url = build_api_url(ats, board_id)
        if verify_ats_url(api_url):
            print(f"  ✅ Found via direct probe: {ats}")
            return {
                "ats": ats,
                "board_id": board_id,
                "board_url": build_board_url(ats, board_id),
                "verified": True
            }
    
    print(f"  ❌ Could not find working ATS URL")
    return None


def repair_company_in_json(company_id: str, companies_path: str = "data/companies.json") -> bool:
    """
    Находит компанию по ID, пытается починить, и обновляет JSON файл.
    """
    path = Path(companies_path)
    
    with open(path, "r") as f:
        companies = json.load(f)
    
    # Найти компанию
    company = None
    company_idx = None
    for i, c in enumerate(companies):
        if c.get("id") == company_id:
            company = c
            company_idx = i
            break
    
    if not company:
        print(f"Company {company_id} not found")
        return False
    
    # Попробовать починить
    result = try_repair_company(company)
    
    if result and result.get("verified"):
        # Обновить данные
        companies[company_idx]["ats"] = result["ats"]
        companies[company_idx]["board_url"] = result["board_url"]
        if result.get("source"):
            companies[company_idx]["careers_url"] = result["source"]
        
        # Сохранить
        with open(path, "w") as f:
            json.dump(companies, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Updated {company_id} in {companies_path}")
        return True
    
    return False


def repair_all_broken(companies_path: str = "data/companies.json", 
                      status_from_api: list = None) -> dict:
    """
    Проходит по всем компаниям с ошибками и пытается их починить.
    
    Args:
        companies_path: путь к companies.json
        status_from_api: список компаний со статусами от /companies endpoint
    
    Returns:
        {"repaired": [...], "failed": [...]}
    """
    path = Path(companies_path)
    
    with open(path, "r") as f:
        companies = json.load(f)
    
    # Определить какие компании сломаны
    broken_ids = set()
    if status_from_api:
        for c in status_from_api:
            if c.get("last_ok") == False:
                broken_ids.add(c.get("id"))
    
    repaired = []
    failed = []
    updated = False
    
    for i, company in enumerate(companies):
        company_id = company.get("id")
        
        # Пропустить если не в списке сломанных (если список предоставлен)
        if status_from_api and company_id not in broken_ids:
            continue
        
        # Проверить текущий URL
        current_url = company.get("board_url", "")
        if current_url and verify_ats_url(current_url):
            continue  # URL работает
        
        # Попробовать починить
        result = try_repair_company(company)
        
        if result and result.get("verified"):
            companies[i]["ats"] = result["ats"]
            companies[i]["board_url"] = result["board_url"]
            if result.get("source"):
                companies[i]["careers_url"] = result["source"]
            
            repaired.append({
                "id": company_id,
                "name": company.get("name"),
                "new_ats": result["ats"],
                "new_url": result["board_url"]
            })
            updated = True
        else:
            failed.append({
                "id": company_id,
                "name": company.get("name")
            })
    
    # Сохранить если были изменения
    if updated:
        with open(path, "w") as f:
            json.dump(companies, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Saved updates to {companies_path}")
    
    return {"repaired": repaired, "failed": failed}


# CLI interface
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "repair":
            # Repair all broken companies
            result = repair_all_broken()
            print(f"\nRepaired: {len(result['repaired'])}")
            print(f"Failed: {len(result['failed'])}")
            
        elif sys.argv[1] == "test":
            # Test detection on a few URLs
            test_urls = [
                "https://openai.com/careers",
                "https://notion.so/careers", 
                "https://stripe.com/jobs",
                "https://linear.app/careers",
            ]
            
            for url in test_urls:
                print(f"\n{url}")
                result = detect_ats(url)
                if result and not result.get("error"):
                    print(f"  ✅ {result['ats']} → {result['board_url'][:60]}...")
                else:
                    print(f"  ❌ {result}")
                    
        elif sys.argv[1] == "check":
            # Check a specific company by name
            if len(sys.argv) > 2:
                company_name = " ".join(sys.argv[2:])
                result = try_repair_company({"name": company_name})
                print(f"\nResult: {result}")
    else:
        print("Usage:")
        print("  python ats_detector.py test     - test detection on sample URLs")
        print("  python ats_detector.py repair   - repair all broken companies")
        print("  python ats_detector.py check <company name> - check specific company")
