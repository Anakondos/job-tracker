#!/usr/bin/env python3
"""
Company Discovery Tool — автоматический поиск компаний с релевантными вакансиями

Использует Claude API для поиска tech-компаний в целевых локациях (NC + Remote US).
Определяет ATS, проверяет наличие релевантных ролей, сохраняет в staging area.

Использование:
    python tools/company_discovery.py search           # AI-поиск + seed list
    python tools/company_discovery.py search --ai-only # только AI-поиск
    python tools/company_discovery.py search --seed-only  # только seed list
    python tools/company_discovery.py list             # показать кандидатов
    python tools/company_discovery.py validate         # проверить ATS + URL
    python tools/company_discovery.py preview          # preview релевантных ролей
"""

import json
import os
import re
import sys
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

COMPANIES_FILE = PROJECT_ROOT / "data" / "companies.json"
STAGING_FILE = PROJECT_ROOT / "data" / "discovered_companies.json"

# Загружаем .env
load_dotenv(PROJECT_ROOT / ".env", override=True)

# Поддерживаемые ATS (из main.py ATS_PARSERS)
SUPPORTED_ATS = ["greenhouse", "lever", "smartrecruiters", "ashby", "workday", "atlassian", "phenom"]

# Целевые роли (из config/roles.json — primary category)
TARGET_ROLES = [
    "Product Manager", "Technical Program Manager", "Program Manager",
    "Product Owner", "Project Manager", "Delivery Manager",
    "Scrum Master", "Release Manager", "Project Lead",
]

# Целевые локации
TARGET_STATE = "NC"
TARGET_CITIES = ["Raleigh", "Durham", "Charlotte", "Cary", "Morrisville", "Research Triangle"]


def load_companies() -> list:
    """Загружаем текущие компании из companies.json"""
    with open(COMPANIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_staging() -> list:
    """Загружаем staging area"""
    if not STAGING_FILE.exists():
        return []
    with open(STAGING_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_staging(candidates: list):
    """Сохраняем staging area"""
    with open(STAGING_FILE, "w", encoding="utf-8") as f:
        json.dump(candidates, f, indent=2, ensure_ascii=False)
    print(f"  💾 Сохранено {len(candidates)} кандидатов в {STAGING_FILE.name}")


def get_existing_ids() -> set:
    """Получаем множество id и имён уже существующих компаний"""
    companies = load_companies()
    ids = set()
    for c in companies:
        ids.add(c.get("id", "").lower())
        ids.add(c.get("name", "").lower())
    return ids


def get_staging_ids() -> set:
    """Получаем множество id кандидатов в staging"""
    return {c.get("id", "").lower() for c in load_staging()}


def call_claude_api(prompt: str, max_tokens: int = 4000) -> str | None:
    """Вызов Claude API"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set in .env")
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except ImportError:
        print("❌ anthropic package not installed. Run: pip install anthropic")
        return None
    except Exception as e:
        print(f"❌ Claude API error: {e}")
        return None


# ===== ATS Detection (из data_cleanup.py) =====

def detect_ats_from_board_url(board_url: str) -> dict | None:
    """Определяем ATS по URL паттерну board_url"""
    if not board_url:
        return None

    parsed = urlparse(board_url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "greenhouse.io" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "greenhouse", "board_url": f"https://boards.greenhouse.io/{slug}"}

    if "lever.co" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "lever", "board_url": f"https://jobs.lever.co/{slug}"}

    if "smartrecruiters.com" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "smartrecruiters", "board_url": f"https://jobs.smartrecruiters.com/{slug}"}

    if "ashbyhq.com" in host:
        slug = path.strip("/").split("/")[0] if path.strip("/") else ""
        if slug:
            return {"ats": "ashby", "board_url": f"https://jobs.ashbyhq.com/{slug}"}

    if "myworkdayjobs.com" in host:
        parts = host.split(".")
        company = parts[0] if parts else ""
        wd_match = re.search(r"\.(wd\d+)\.", host)
        wd_num = wd_match.group(1) if wd_match else "wd1"
        site_match = re.search(r"myworkdayjobs\.com/(?:[a-z][a-z]-[A-Z][A-Z]/)?([^/]+)", board_url)
        site = site_match.group(1) if site_match else company
        return {"ats": "workday", "board_url": f"https://{company}.{wd_num}.myworkdayjobs.com/{site}"}

    if "icims.com" in host:
        subdomain = host.split(".")[0]
        return {"ats": "icims", "board_url": f"https://{subdomain}.icims.com/jobs"}

    return None


def try_detect_ats_via_http(board_url: str) -> dict | None:
    """Определяем ATS через HTTP запрос — проверяем редиректы и HTML"""
    try:
        r = requests.get(board_url, timeout=15, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        final_url = r.url.lower()

        # Проверяем редирект
        redirect_result = detect_ats_from_board_url(final_url)
        if redirect_result:
            return redirect_result

        # Ищем паттерны в HTML
        html = r.text[:10000].lower()

        ats_patterns = {
            "greenhouse": [r"boards\.greenhouse\.io/([a-z0-9_-]+)"],
            "lever": [r"jobs\.lever\.co/([a-z0-9_-]+)"],
            "ashby": [r"jobs\.ashbyhq\.com/([a-z0-9_-]+)"],
            "smartrecruiters": [r"jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)"],
        }

        for ats, patterns in ats_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    slug = match.group(1)
                    url_templates = {
                        "greenhouse": f"https://boards.greenhouse.io/{slug}",
                        "lever": f"https://jobs.lever.co/{slug}",
                        "ashby": f"https://jobs.ashbyhq.com/{slug}",
                        "smartrecruiters": f"https://jobs.smartrecruiters.com/{slug}",
                    }
                    return {"ats": ats, "board_url": url_templates[ats]}

    except Exception as e:
        print(f"    ⚠️ HTTP ошибка для {board_url}: {e}")

    return None


def detect_and_validate(careers_url: str) -> dict:
    """
    Определяем ATS и валидируем URL.
    Возвращает: {ats, board_url, ats_verified, supported}
    """
    result = {"ats": None, "board_url": careers_url, "ats_verified": False, "supported": False}

    # Шаг 1: URL паттерн
    detected = detect_ats_from_board_url(careers_url)
    if detected:
        result.update(detected)
        result["ats_verified"] = True
        result["supported"] = detected["ats"] in SUPPORTED_ATS
        return result

    # Шаг 2: HTTP запрос
    detected = try_detect_ats_via_http(careers_url)
    if detected:
        result.update(detected)
        result["ats_verified"] = True
        result["supported"] = detected["ats"] in SUPPORTED_ATS
        return result

    result["ats"] = "unknown"
    return result


# ===== AI Discovery =====

def build_discovery_prompt(existing_names: set) -> str:
    """Формируем промпт для AI-поиска компаний"""
    existing_list = ", ".join(sorted(list(existing_names))[:50])

    prompt = f"""Я ищу tech-компании для отслеживания вакансий Product Manager, TPM, Program Manager, Project Manager.

Фокус: Северная Каролина (NC) — Raleigh, Durham, Charlotte, Research Triangle Park.
Также: крупные US tech-компании с remote-позициями.

У меня УЖЕ есть эти компании (НЕ включай их):
{existing_list}

Мне нужны НОВЫЕ компании, у которых:
1. Есть карьерная страница / job board (обязательно)
2. Вероятно нанимают PM/TPM/Program Manager
3. Используют один из ATS: Greenhouse, Lever, SmartRecruiters, Ashby, Workday (предпочтительно)

Для каждой компании укажи:
- name: полное название
- careers_url: URL карьерной страницы (РЕАЛЬНЫЙ, проверяемый URL)
- hq_state: штат штаб-квартиры (2 буквы) или null
- industry: IT, Fintech, Healthcare, Consulting, Gaming, Retail, Banking, Security, AI/ML, Data, DevTools, Enterprise SaaS, Cloud, E-commerce, Social, EdTech, Hardware, Biotech, Telecommunications, Manufacturing, Other
- tags: 1-3 тега из [security, devtools, cloud, ai, data, fintech, saas, hr, enterprise, gaming, healthtech, biotech, consulting, software, edtech, payments, analytics, ecommerce, hardware, retail]
- reasoning: почему эта компания подходит (1 предложение)

ВАЖНО:
- Приоритет #1: NC компании с офисами в Research Triangle / Charlotte
- Приоритет #2: Крупные US tech с remote-friendly PM ролями
- Давай 15-25 компаний
- URL должны быть РЕАЛЬНЫМИ (не придумывай)
- Не включай компании из моего списка

Ответь СТРОГО в формате JSON-массива без markdown-блоков:
[
  {{"name": "...", "careers_url": "...", "hq_state": "NC", "industry": "IT", "tags": ["software"], "reasoning": "..."}},
  ...
]
"""
    return prompt


def parse_discovery_response(response: str) -> list:
    """Парсим JSON ответ от Claude"""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError as e:
        print(f"  ⚠️ Ошибка парсинга JSON: {e}")
        print(f"  Ответ: {text[:200]}...")
    return []


def discover_via_ai(existing_ids: set, existing_names: set) -> list:
    """Поиск компаний через Claude API"""
    print("\n🤖 AI-поиск компаний...")

    prompt = build_discovery_prompt(existing_names)
    response = call_claude_api(prompt, max_tokens=6000)

    if not response:
        print("  ❌ Нет ответа от Claude API")
        return []

    suggestions = parse_discovery_response(response)
    print(f"  📋 Claude предложил {len(suggestions)} компаний")

    candidates = []
    for s in suggestions:
        name = s.get("name", "")
        careers_url = s.get("careers_url", "")

        if not name or not careers_url:
            continue

        # Генерируем id
        cid = re.sub(r"[^a-z0-9]", "-", name.lower()).strip("-")
        cid = re.sub(r"-+", "-", cid)

        # Пропускаем если уже существует
        if cid in existing_ids or name.lower() in existing_ids:
            print(f"    ⏭️ {name} — уже существует")
            continue

        candidates.append({
            "id": cid,
            "name": name,
            "careers_url": careers_url,
            "hq_state": s.get("hq_state"),
            "industry": s.get("industry", ""),
            "tags": s.get("tags", []),
            "discovery_source": "ai_suggestion",
            "reasoning": s.get("reasoning", ""),
            "discovered_at": datetime.now().isoformat(),
            "ats": None,
            "board_url": None,
            "ats_verified": False,
            "supported": False,
            "relevant_roles_count": None,
            "status": "pending_validation",
        })

    return candidates


# ===== Seed List (курированный список NC tech-компаний) =====

# Компании из NC / Research Triangle которые вероятно нанимают PM
NC_SEED_LIST = [
    {"name": "Epic Games", "careers_url": "https://boards.greenhouse.io/epicgames", "hq_state": "NC", "industry": "Gaming", "tags": ["gaming", "software"]},
    {"name": "Cisco (RTP)", "careers_url": "https://jobs.cisco.com", "hq_state": "CA", "industry": "IT", "tags": ["networking", "enterprise"]},
    {"name": "IBM (RTP)", "careers_url": "https://www.ibm.com/careers", "hq_state": "NY", "industry": "IT", "tags": ["enterprise", "cloud", "ai"]},
    {"name": "NetApp", "careers_url": "https://jobs.smartrecruiters.com/NetApp", "hq_state": "CA", "industry": "IT", "tags": ["storage", "cloud"]},
    {"name": "Fidelity Investments (Durham)", "careers_url": "https://jobs.fidelity.com", "hq_state": "MA", "industry": "Fintech", "tags": ["fintech", "investment"]},
    {"name": "MetLife (Cary)", "careers_url": "https://jobs.metlife.com", "hq_state": "NY", "industry": "Fintech", "tags": ["fintech", "enterprise"]},
    {"name": "Lenovo (Morrisville)", "careers_url": "https://jobs.lenovo.com", "hq_state": "NC", "industry": "Hardware", "tags": ["hardware", "enterprise"]},
    {"name": "Credit Suisse (RTP)", "careers_url": "https://www.credit-suisse.com/careers", "hq_state": None, "industry": "Banking", "tags": ["banking", "fintech"]},
    {"name": "Allscripts (Raleigh)", "careers_url": "https://www.veracitynetworks.com/careers", "hq_state": "NC", "industry": "Healthcare", "tags": ["healthtech"]},
    {"name": "Dude Solutions", "careers_url": "https://boards.greenhouse.io/dudesolutions", "hq_state": "NC", "industry": "Enterprise SaaS", "tags": ["saas", "enterprise"]},
    {"name": "Spreedly", "careers_url": "https://boards.greenhouse.io/spreedly", "hq_state": "NC", "industry": "Fintech", "tags": ["fintech", "payments"]},
    {"name": "iCIMS", "careers_url": "https://careers.icims.com", "hq_state": "NJ", "industry": "Enterprise SaaS", "tags": ["saas", "hr"]},
    {"name": "Avalara", "careers_url": "https://jobs.lever.co/avalara", "hq_state": "WA", "industry": "Fintech", "tags": ["fintech", "saas", "compliance"]},
    {"name": "Cree (Durham)", "careers_url": "https://www.wolfspeed.com/company/careers", "hq_state": "NC", "industry": "Hardware", "tags": ["hardware"]},
    {"name": "Prometheus Group", "careers_url": "https://boards.greenhouse.io/prometheusgroup", "hq_state": "NC", "industry": "Enterprise SaaS", "tags": ["saas", "enterprise"]},
    {"name": "Relay (Raleigh)", "careers_url": "https://boards.greenhouse.io/relaypro", "hq_state": "NC", "industry": "IT", "tags": ["hardware", "communications"]},
    {"name": "Windsor Group (Raleigh)", "careers_url": "https://www.thewindsorgroup.com/careers", "hq_state": "NC", "industry": "Consulting", "tags": ["consulting"]},
    {"name": "Arch Capital Services", "careers_url": "https://archgroup.wd5.myworkdayjobs.com/Arch", "hq_state": "NC", "industry": "Fintech", "tags": ["fintech"]},
    {"name": "nCino (Wilmington NC)", "careers_url": "https://boards.greenhouse.io/ncino", "hq_state": "NC", "industry": "Fintech", "tags": ["fintech", "banking", "saas"]},
    {"name": "Lowe's (Mooresville NC)", "careers_url": "https://talent.lowes.com", "hq_state": "NC", "industry": "Retail", "tags": ["retail", "ecommerce"]},
]


def discover_from_seed_list(existing_ids: set) -> list:
    """Добавляем компании из курированного seed list"""
    print("\n📋 Проверяем seed list NC-компаний...")

    staging_ids = get_staging_ids()
    candidates = []

    for seed in NC_SEED_LIST:
        name = seed["name"]
        careers_url = seed["careers_url"]

        # Генерируем id
        # Для seed используем чистое имя без скобок
        clean_name = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
        cid = re.sub(r"[^a-z0-9]", "-", clean_name.lower()).strip("-")
        cid = re.sub(r"-+", "-", cid)

        # Пропускаем если уже существует
        if cid in existing_ids or name.lower() in existing_ids:
            print(f"    ⏭️ {name} — уже в companies.json")
            continue
        if cid in staging_ids:
            print(f"    ⏭️ {name} — уже в staging")
            continue

        candidates.append({
            "id": cid,
            "name": name,
            "careers_url": careers_url,
            "hq_state": seed.get("hq_state"),
            "industry": seed.get("industry", ""),
            "tags": seed.get("tags", []),
            "discovery_source": "seed_list_nc",
            "reasoning": f"NC tech company seed list",
            "discovered_at": datetime.now().isoformat(),
            "ats": None,
            "board_url": None,
            "ats_verified": False,
            "supported": False,
            "relevant_roles_count": None,
            "status": "pending_validation",
        })
        print(f"    ➕ {name} ({careers_url})")

    return candidates


# ===== Validation =====

def validate_candidates(candidates: list) -> int:
    """Проверяем ATS для кандидатов со статусом pending_validation"""
    changes = 0
    pending = [c for c in candidates if c.get("status") == "pending_validation"]

    print(f"\n🔍 Валидация {len(pending)} кандидатов...")

    for c in pending:
        cid = c["id"]
        careers_url = c.get("careers_url", "")
        print(f"  [{cid}] {careers_url}")

        result = detect_and_validate(careers_url)

        c["ats"] = result["ats"]
        c["board_url"] = result["board_url"]
        c["ats_verified"] = result["ats_verified"]
        c["supported"] = result["supported"]

        if result["supported"]:
            c["status"] = "validated"
            print(f"    ✅ {result['ats']} → {result['board_url']}")
        elif result["ats"] and result["ats"] != "unknown":
            c["status"] = "unsupported_ats"
            print(f"    🟡 {result['ats']} (не поддерживается)")
        else:
            c["status"] = "no_ats_detected"
            print(f"    ❌ ATS не определён")

        changes += 1

    return changes


# ===== Preview Roles =====

def preview_relevant_roles(candidates: list) -> int:
    """
    Для validated кандидатов — быстрый парсинг вакансий и подсчёт релевантных ролей.
    Используем парсеры напрямую (без refresh_company_sync).
    """
    validated = [c for c in candidates if c.get("status") == "validated" and c.get("supported")]

    if not validated:
        print("\n📊 Нет validated кандидатов для preview")
        return 0

    print(f"\n📊 Preview ролей для {len(validated)} кандидатов...")

    # Импортируем парсеры
    try:
        from parsers.greenhouse import fetch_greenhouse
        from parsers.lever import fetch_lever
        from parsers.smartrecruiters import fetch_smartrecruiters
        from parsers.ashby import fetch_ashby_jobs
        from parsers.workday import fetch_workday
    except ImportError as e:
        print(f"  ❌ Ошибка импорта парсеров: {e}")
        return 0

    ats_fetchers = {
        "greenhouse": lambda url: fetch_greenhouse("", url),
        "lever": lambda url: fetch_lever("", url),
        "smartrecruiters": lambda url: fetch_smartrecruiters("", url),
        "ashby": fetch_ashby_jobs,
        "workday": lambda url: fetch_workday("", url),
    }

    # Ключевые слова для ролей
    role_keywords = [
        "product manager", "program manager", "project manager",
        "tpm", "technical program", "product owner",
        "delivery manager", "scrum master", "release manager",
        "project lead",
    ]

    changes = 0
    for c in validated:
        cid = c["id"]
        ats = c["ats"]
        board_url = c["board_url"]

        fetcher = ats_fetchers.get(ats)
        if not fetcher:
            continue

        print(f"  [{cid}] Парсим {ats}: {board_url}...")

        try:
            jobs = fetcher(board_url)
            total_jobs = len(jobs) if jobs else 0

            # Считаем релевантные роли
            relevant = 0
            relevant_titles = []
            for job in (jobs or []):
                title = job.get("title", "").lower()
                if any(kw in title for kw in role_keywords):
                    relevant += 1
                    relevant_titles.append(job.get("title", ""))

            c["relevant_roles_count"] = relevant
            c["total_jobs_count"] = total_jobs
            c["relevant_titles_sample"] = relevant_titles[:5]

            if relevant > 0:
                c["status"] = "ready_to_approve"
                print(f"    ✅ {total_jobs} вакансий, {relevant} релевантных")
                for t in relevant_titles[:3]:
                    print(f"       • {t}")
            else:
                c["status"] = "no_relevant_roles"
                print(f"    ⚠️ {total_jobs} вакансий, 0 релевантных ролей")

            changes += 1
        except Exception as e:
            print(f"    ❌ Ошибка парсинга: {e}")
            c["status"] = "parse_error"
            c["error"] = str(e)
            changes += 1

    return changes


# ===== List Candidates =====

def list_candidates():
    """Показать список кандидатов из staging"""
    candidates = load_staging()

    if not candidates:
        print("\n📋 Staging пуст. Запустите: python tools/company_discovery.py search")
        return

    # Группируем по статусу
    by_status = {}
    for c in candidates:
        status = c.get("status", "unknown")
        by_status.setdefault(status, []).append(c)

    status_emojis = {
        "ready_to_approve": "✅",
        "validated": "🔍",
        "pending_validation": "⏳",
        "unsupported_ats": "🟡",
        "no_ats_detected": "❌",
        "no_relevant_roles": "⚠️",
        "parse_error": "💥",
        "approved": "🎉",
        "rejected": "🚫",
    }

    print(f"\n📋 Discovered Companies ({len(candidates)} total)")
    print("=" * 60)

    for status, items in sorted(by_status.items()):
        emoji = status_emojis.get(status, "❓")
        print(f"\n{emoji} {status} ({len(items)}):")
        for c in items:
            ats = c.get("ats", "?")
            roles = c.get("relevant_roles_count")
            roles_str = f", {roles} PM roles" if roles is not None else ""
            source = c.get("discovery_source", "?")
            print(f"  [{c['id']}] {c.get('name')} — ats={ats}{roles_str} (from: {source})")


# ===== Main =====

def main():
    if len(sys.argv) < 2:
        print("""
Company Discovery Tool
======================

Usage:
  python tools/company_discovery.py search            # AI + seed list поиск
  python tools/company_discovery.py search --ai-only  # только AI
  python tools/company_discovery.py search --seed-only # только seed list
  python tools/company_discovery.py validate           # проверить ATS
  python tools/company_discovery.py preview            # парсим вакансии
  python tools/company_discovery.py list               # список кандидатов

Pipeline: search → validate → preview → approve (через API)
        """)
        return

    command = sys.argv[1]

    if command == "search":
        ai_only = "--ai-only" in sys.argv
        seed_only = "--seed-only" in sys.argv

        print("\n🔎 Company Discovery")
        print("=" * 50)

        existing_ids = get_existing_ids()
        existing_names = {c.get("name", "") for c in load_companies()}
        candidates = load_staging()
        initial_count = len(candidates)

        new_candidates = []

        if not seed_only:
            ai_candidates = discover_via_ai(existing_ids, existing_names)
            new_candidates.extend(ai_candidates)
            print(f"  🤖 AI: {len(ai_candidates)} новых кандидатов")

        if not ai_only:
            seed_candidates = discover_from_seed_list(existing_ids)
            new_candidates.extend(seed_candidates)
            print(f"  📋 Seed: {len(seed_candidates)} новых кандидатов")

        # Дедупликация с staging
        staging_ids = {c.get("id") for c in candidates}
        added = 0
        for nc in new_candidates:
            if nc["id"] not in staging_ids:
                candidates.append(nc)
                staging_ids.add(nc["id"])
                added += 1

        save_staging(candidates)
        print(f"\n📝 Добавлено {added} новых кандидатов (было {initial_count}, стало {len(candidates)})")
        print("\nСледующий шаг: python tools/company_discovery.py validate")

    elif command == "validate":
        candidates = load_staging()
        if not candidates:
            print("📋 Staging пуст")
            return

        changes = validate_candidates(candidates)
        save_staging(candidates)

        # Статистика
        supported = sum(1 for c in candidates if c.get("supported"))
        unsupported = sum(1 for c in candidates if c.get("status") == "unsupported_ats")
        no_ats = sum(1 for c in candidates if c.get("status") == "no_ats_detected")
        print(f"\n📊 Итого: {supported} с поддерживаемым ATS, {unsupported} unsupported, {no_ats} не определён")
        print("\nСледующий шаг: python tools/company_discovery.py preview")

    elif command == "preview":
        candidates = load_staging()
        if not candidates:
            print("📋 Staging пуст")
            return

        changes = preview_relevant_roles(candidates)
        save_staging(candidates)

        ready = sum(1 for c in candidates if c.get("status") == "ready_to_approve")
        no_roles = sum(1 for c in candidates if c.get("status") == "no_relevant_roles")
        print(f"\n📊 Итого: {ready} ready to approve, {no_roles} без релевантных ролей")

    elif command == "list":
        list_candidates()

    else:
        print(f"❌ Неизвестная команда: {command}")
        print("Доступные: search, validate, preview, list")


if __name__ == "__main__":
    main()
