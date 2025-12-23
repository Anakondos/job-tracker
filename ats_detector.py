"""
ATS Detector - автоматическое обнаружение ATS компании по careers странице
"""
import re
import requests
from urllib.parse import urlparse

ATS_PATTERNS = {
    "greenhouse": [
        r"boards\.greenhouse\.io/(\w+)",
        r"boards-api\.greenhouse\.io/v1/boards/(\w+)",
        r"greenhouse\.io.*?/(\w+)/jobs",
    ],
    "lever": [
        r"jobs\.lever\.co/(\w+)",
        r"api\.lever\.co/v0/postings/(\w+)",
    ],
    "smartrecruiters": [
        r"jobs\.smartrecruiters\.com/(\w+)",
        r"careers\.smartrecruiters\.com/(\w+)",
    ],
    "ashby": [
        r"jobs\.ashbyhq\.com/(\w+)",
    ],
    "workday": [
        r"(\w+)\.wd\d+\.myworkdayjobs\.com",
    ],
}

def detect_ats(careers_url: str) -> dict | None:
    """
    Загружает careers страницу и ищет ссылки на ATS.
    Возвращает {"ats": "greenhouse", "board_id": "openai", "board_url": "..."} или None
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0)"}
        resp = requests.get(careers_url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        
        # Также проверяем финальный URL после редиректов
        final_url = resp.url
        urls_to_check = [html, final_url]
        
        for ats_name, patterns in ATS_PATTERNS.items():
            for pattern in patterns:
                for text in urls_to_check:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        board_id = match.group(1).lower()
                        board_url = build_board_url(ats_name, board_id)
                        return {
                            "ats": ats_name,
                            "board_id": board_id,
                            "board_url": board_url,
                            "source": careers_url
                        }
        
        return None
        
    except Exception as e:
        return {"error": str(e)}


def build_board_url(ats: str, board_id: str) -> str:
    """Строит API URL для ATS"""
    if ats == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs"
    elif ats == "lever":
        return f"https://api.lever.co/v0/postings/{board_id}?mode=json"
    elif ats == "smartrecruiters":
        return f"https://jobs.smartrecruiters.com/{board_id}"
    elif ats == "ashby":
        return f"https://jobs.ashbyhq.com/{board_id}"
    else:
        return ""


def verify_ats_url(ats: str, board_url: str) -> bool:
    """Проверяет что ATS URL работает"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0)"}
        resp = requests.get(board_url, headers=headers, timeout=10)
        return resp.status_code == 200
    except:
        return False


# Quick test
if __name__ == "__main__":
    test_urls = [
        "https://openai.com/careers",
        "https://www.notion.so/careers",
        "https://www.shopify.com/careers",
    ]
    
    for url in test_urls:
        print(f"\n{url}")
        result = detect_ats(url)
        print(f"  → {result}")
