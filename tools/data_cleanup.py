#!/usr/bin/env python3
"""
Data Cleanup Tool — чистка data/companies.json

Исправляет:
- Битые URL (опечатки в board_url)
- Дубли компаний (disable дубль, оставляем основную)
- Мусорные записи (не компании)
- Компании с постоянными 404 ошибками

Использование:
    python tools/data_cleanup.py --dry-run      # предпросмотр изменений
    python tools/data_cleanup.py                # применить изменения
    python tools/data_cleanup.py --triage-universal  # триаж universal-компаний
"""

import json
import re
import sys
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).parent.parent
COMPANIES_FILE = PROJECT_ROOT / "data" / "companies.json"
STATUS_FILE = PROJECT_ROOT / "data" / "company_status.json"


def load_companies() -> list:
    """Загружаем companies.json"""
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_companies(companies: list):
    """Сохраняем companies.json"""
    with open(COMPANIES_FILE, "w", encoding="utf-8") as f:
        json.dump(companies, f, indent=2, ensure_ascii=False)
    print(f"✅ Сохранено {len(companies)} компаний в {COMPANIES_FILE}")


def load_status() -> dict:
    """Загружаем company_status.json"""
    if not STATUS_FILE.exists():
        return {}
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def verify_url(url: str, timeout: int = 10) -> bool:
    """Проверяем доступность URL (HTTP 200)"""
    try:
        # Для greenhouse API
        if "boards.greenhouse.io" in url:
            token = url.rstrip("/").split("/")[-1]
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            r = requests.get(api_url, timeout=timeout)
            return r.status_code == 200
        # Для lever
        if "jobs.lever.co" in url:
            slug = url.rstrip("/").split("/")[-1]
            api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
            r = requests.get(api_url, timeout=timeout)
            return r.status_code == 200
        # Общая проверка
        r = requests.head(url, timeout=timeout, allow_redirects=True)
        return r.status_code < 400
    except Exception as e:
        print(f"  ⚠️ Ошибка проверки {url}: {e}")
        return False


# ===== Правила исправления =====

# a) Битые URL — {id: {field: new_value}}
URL_FIXES = {
    "insightsoftware": {
        "board_url": "https://boards.greenhouse.io/insightsoftware",
    },
}

# b) Дубли — disable дубль, оставляем основную
#    {duplicate_id: reason}
DUPLICATES_TO_DISABLE = {
    "wellsfargo": "Дубль wells_fargo (workday)",
    "apply": "Дубль deloitte (оба → apply.deloitte.com)",
    "external-firstcitizens": "Дубль firstcitizens (icims)",
}

# c) Мусорные записи — не являются компаниями
JUNK_ENTRIES = {
    "boards": "boards.greenhouse.io — не компания, а платформа",
    "job-boards": "job-boards.greenhouse.io — не компания",
    "wd1": "wd1.myworkdaysite.com — не компания, а платформа",
    # ashbyhq уже disabled
}

# d) Компании с постоянными 404
BROKEN_COMPANIES = {
    "k4connect": "ats_404",
    "fmsystems": "ats_404",
    "analytics8": "ats_404",
    "protect-ai": "ats_404",
    "kevel": "ats_404",
    "imaginovation": "ats_404",
}

# e) Компании для расследования (ранее работали, теперь ошибки)
NEEDS_INVESTIGATION = {
    "bosch": "SmartRecruiters JSON parse error",
    "visa": "SmartRecruiters JSON parse error",
}


def fix_urls(companies: list, dry_run: bool) -> int:
    """Исправляем опечатки в URL"""
    changes = 0
    for c in companies:
        cid = c.get("id")
        if cid in URL_FIXES:
            for field, new_value in URL_FIXES[cid].items():
                old_value = c.get(field)
                if old_value != new_value:
                    print(f"  🔧 [{cid}] {field}: '{old_value}' → '{new_value}'")
                    if not dry_run:
                        # Проверяем новый URL перед применением
                        if field == "board_url" and not verify_url(new_value):
                            print(f"    ❌ Новый URL недоступен, пропускаем!")
                            continue
                        c[field] = new_value
                    changes += 1
    return changes


def disable_duplicates(companies: list, dry_run: bool) -> int:
    """Disable дублей компаний"""
    changes = 0
    for c in companies:
        cid = c.get("id")
        if cid in DUPLICATES_TO_DISABLE and c.get("enabled", True):
            reason = DUPLICATES_TO_DISABLE[cid]
            print(f"  🔄 [{cid}] enabled → false (причина: {reason})")
            if not dry_run:
                c["enabled"] = False
                c["status"] = "duplicate"
            changes += 1
    return changes


def disable_junk(companies: list, dry_run: bool) -> int:
    """Disable мусорных записей"""
    changes = 0
    for c in companies:
        cid = c.get("id")
        if cid in JUNK_ENTRIES and c.get("enabled", True):
            reason = JUNK_ENTRIES[cid]
            print(f"  🗑️ [{cid}] enabled → false (причина: {reason})")
            if not dry_run:
                c["enabled"] = False
                c["status"] = "not_a_company"
            changes += 1
    return changes


def disable_broken(companies: list, dry_run: bool) -> int:
    """Disable компаний с постоянными 404"""
    changes = 0
    for c in companies:
        cid = c.get("id")
        if cid in BROKEN_COMPANIES and c.get("enabled", True):
            new_status = BROKEN_COMPANIES[cid]
            print(f"  ❌ [{cid}] enabled → false, status → {new_status}")
            if not dry_run:
                c["enabled"] = False
                c["status"] = new_status
            changes += 1
    return changes


def mark_investigation(companies: list, dry_run: bool) -> int:
    """Помечаем компании для расследования"""
    changes = 0
    for c in companies:
        cid = c.get("id")
        if cid in NEEDS_INVESTIGATION and c.get("status") != "needs_investigation":
            reason = NEEDS_INVESTIGATION[cid]
            print(f"  🔍 [{cid}] status → needs_investigation ({reason})")
            if not dry_run:
                c["status"] = "needs_investigation"
            changes += 1
    return changes


# ===== Поддерживаемые ATS (из main.py ATS_PARSERS) =====
SUPPORTED_ATS = ["greenhouse", "lever", "smartrecruiters", "ashby", "workday", "atlassian", "phenom"]


def detect_ats_from_board_url(board_url: str) -> dict | None:
    """
    Определяем ATS по board_url компании.
    Упрощённая версия detect_ats_from_url() из main.py — работает с board_url, не с job URL.
    Возвращает {ats, board_url} если определён поддерживаемый ATS, иначе None.
    """
    if not board_url:
        return None

    parsed = urlparse(board_url)
    host = parsed.netloc.lower()
    path = parsed.path

    # Greenhouse: boards.greenhouse.io/company
    if "greenhouse.io" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "greenhouse", "board_url": f"https://boards.greenhouse.io/{slug}"}

    # Lever: jobs.lever.co/company
    if "lever.co" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "lever", "board_url": f"https://jobs.lever.co/{slug}"}

    # SmartRecruiters: jobs.smartrecruiters.com/Company
    if "smartrecruiters.com" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "smartrecruiters", "board_url": f"https://jobs.smartrecruiters.com/{slug}"}

    # Ashby: jobs.ashbyhq.com/company
    if "ashbyhq.com" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "ashby", "board_url": f"https://jobs.ashbyhq.com/{slug}"}

    # Workday: company.wd1.myworkdayjobs.com/site
    if "myworkdayjobs.com" in host:
        parts = host.split(".")
        company = parts[0] if parts else ""
        wd_match = re.search(r"\.(wd\d+)\.", host)
        wd_num = wd_match.group(1) if wd_match else "wd1"
        site_match = re.search(r"myworkdayjobs\.com/(?:[a-z][a-z]-[A-Z][A-Z]/)?([^/]+)", board_url)
        site = site_match.group(1) if site_match else company
        return {"ats": "workday", "board_url": f"https://{company}.{wd_num}.myworkdayjobs.com/{site}"}

    # iCIMS: external-company.icims.com/jobs
    if "icims.com" in host:
        subdomain = host.split(".")[0]
        return {"ats": "icims", "board_url": f"https://{subdomain}.icims.com/jobs"}

    # Phenom: обычно company.wd1.myworkdayjobs.com или careers.company.com с phenom JS
    # Phenom определяется по содержимому, здесь не можем — пропускаем

    return None


def try_detect_ats_via_http(board_url: str) -> dict | None:
    """
    Пробуем определить ATS через HTTP-запрос к board_url.
    Ищем паттерны в HTML/редиректах.
    """
    try:
        r = requests.get(board_url, timeout=15, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        final_url = r.url.lower()

        # Проверяем редирект на известный ATS
        redirect_result = detect_ats_from_board_url(final_url)
        if redirect_result and redirect_result["ats"] in SUPPORTED_ATS:
            return redirect_result

        # Ищем паттерны в HTML
        html = r.text[:10000].lower()

        # Greenhouse embed
        if "boards.greenhouse.io" in html or "greenhouse.io/embed" in html:
            match = re.search(r"boards\.greenhouse\.io/([a-z0-9_-]+)", html)
            if match:
                slug = match.group(1)
                return {"ats": "greenhouse", "board_url": f"https://boards.greenhouse.io/{slug}"}

        # Lever embed
        if "jobs.lever.co" in html or "lever.co/embed" in html:
            match = re.search(r"jobs\.lever\.co/([a-z0-9_-]+)", html)
            if match:
                slug = match.group(1)
                return {"ats": "lever", "board_url": f"https://jobs.lever.co/{slug}"}

        # Ashby embed
        if "jobs.ashbyhq.com" in html:
            match = re.search(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", html)
            if match:
                slug = match.group(1)
                return {"ats": "ashby", "board_url": f"https://jobs.ashbyhq.com/{slug}"}

        # SmartRecruiters embed
        if "jobs.smartrecruiters.com" in html:
            match = re.search(r"jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", html)
            if match:
                slug = match.group(1)
                return {"ats": "smartrecruiters", "board_url": f"https://jobs.smartrecruiters.com/{slug}"}

    except Exception as e:
        print(f"    ⚠️ HTTP ошибка: {e}")

    return None


def triage_universal(companies: list, dry_run: bool) -> int:
    """
    Триаж universal-компаний:
    1. Определяем ATS по URL паттерну (detect_ats_from_board_url)
    2. Если не нашли — пробуем HTTP запрос (try_detect_ats_via_http)
    3. Если нашли поддерживаемый ATS — обновляем ats + board_url
    4. Если нашли неподдерживаемый (icims, zoho, etc.) — помечаем статус
    5. Если не определили — disable + status="unsupported_ats"
    """
    changes = 0
    universal = [c for c in companies if c.get("ats") == "universal" and c.get("enabled", True)]

    print(f"\n📋 Найдено {len(universal)} enabled universal-компаний\n")

    for c in universal:
        cid = c["id"]
        board_url = c.get("board_url", "")
        print(f"  [{cid}] {board_url}")

        # Шаг 1: определяем ATS по URL паттерну
        result = detect_ats_from_board_url(board_url)

        # Шаг 2: если не нашли — HTTP запрос
        if not result and not dry_run:
            print(f"    🔍 Проверяем через HTTP...")
            result = try_detect_ats_via_http(board_url)

        if result:
            new_ats = result["ats"]
            new_board_url = result["board_url"]

            if new_ats in SUPPORTED_ATS:
                # Поддерживаемый ATS — обновляем
                print(f"    ✅ ATS определён: {new_ats} → board_url: {new_board_url}")
                if not dry_run:
                    c["ats"] = new_ats
                    c["board_url"] = new_board_url
                changes += 1
            else:
                # Неподдерживаемый ATS (icims, etc.) — помечаем но не disable
                print(f"    🟡 ATS определён: {new_ats} (не поддерживается)")
                if not dry_run:
                    c["ats"] = new_ats
                    c["board_url"] = new_board_url
                    c["status"] = "unsupported_ats"
                changes += 1
        else:
            # Не определили ATS — disable
            if dry_run:
                print(f"    ❓ ATS не определён (dry-run, HTTP не вызывается)")
            else:
                print(f"    ❌ ATS не определён → disable")
                c["enabled"] = False
                c["status"] = "unsupported_ats"
                changes += 1

    return changes


def print_summary(companies: list):
    """Печатаем статистику"""
    total = len(companies)
    enabled = sum(1 for c in companies if c.get("enabled", True))
    disabled = total - enabled
    no_tags = sum(1 for c in companies if not c.get("tags") and c.get("enabled", True))
    no_industry = sum(1 for c in companies if not c.get("industry") and c.get("enabled", True))
    universal = sum(1 for c in companies if c.get("ats") == "universal" and c.get("enabled", True))

    print(f"\n📊 Статистика:")
    print(f"  Всего компаний: {total}")
    print(f"  Enabled: {enabled}")
    print(f"  Disabled: {disabled}")
    print(f"  Без тегов (enabled): {no_tags}")
    print(f"  Без industry (enabled): {no_industry}")
    print(f"  ATS=universal (enabled): {universal}")


def main():
    dry_run = "--dry-run" in sys.argv
    triage = "--triage-universal" in sys.argv

    if triage:
        # Phase 1.3 — триаж universal-компаний
        mode_t = "DRY RUN" if dry_run else "APPLY"
        print(f"\n🔄 Триаж universal-компаний [{mode_t}]")
        print("=" * 50)

        companies = load_companies()
        print(f"📂 Загружено {len(companies)} компаний")

        changes = triage_universal(companies, dry_run)

        print(f"\n{'=' * 50}")
        print(f"📝 Изменено: {changes} компаний")

        if dry_run:
            print("⚠️ DRY RUN — изменения НЕ сохранены")
            print("Запустите без --dry-run для применения:")
            print("  python tools/data_cleanup.py --triage-universal")
        elif changes > 0:
            save_companies(companies)

        print_summary(load_companies() if not dry_run and changes > 0 else companies)
        return

    mode = "DRY RUN" if dry_run else "APPLY"
    print(f"\n🧹 Data Cleanup Tool [{mode}]")
    print("=" * 50)

    companies = load_companies()
    print(f"📂 Загружено {len(companies)} компаний")

    total_changes = 0

    print(f"\n--- a) Исправление битых URL ---")
    total_changes += fix_urls(companies, dry_run)

    print(f"\n--- b) Disable дублей ---")
    total_changes += disable_duplicates(companies, dry_run)

    print(f"\n--- c) Disable мусорных записей ---")
    total_changes += disable_junk(companies, dry_run)

    print(f"\n--- d) Disable сломанных компаний (404) ---")
    total_changes += disable_broken(companies, dry_run)

    print(f"\n--- e) Пометить для расследования ---")
    total_changes += mark_investigation(companies, dry_run)

    print(f"\n{'=' * 50}")
    print(f"📝 Всего изменений: {total_changes}")

    if dry_run:
        print(f"\n⚠️ DRY RUN — изменения НЕ сохранены")
        print(f"Запустите без --dry-run для применения")
    elif total_changes > 0:
        save_companies(companies)
    else:
        print("Нет изменений для применения")

    # Показываем статистику после изменений
    if not dry_run and total_changes > 0:
        companies = load_companies()  # перечитываем
    print_summary(companies)


if __name__ == "__main__":
    main()
