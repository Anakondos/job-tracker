import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
PROFILES_DIR = BASE_DIR / "profiles"
DATA_DIR = BASE_DIR / "data"  # ВАЖНО: data рядом с storage.py (если у тебя data/ на корне проекта — оставь так)
STATUS_FILE = BASE_DIR / "job_status.json"
HIDE_FILE = BASE_DIR / "job_hide.json"


def _ensure_profiles_dir():
    PROFILES_DIR.mkdir(exist_ok=True)


def load_companies_master():
    """
    Загружает мастер-список компаний из data/companies.json.
    Возвращает два словаря:
    - по id
    - по имени (lowercase)
    """
    companies_path = DATA_DIR / "companies.json"
    if not companies_path.exists():
        return {}, {}

    with companies_path.open("r", encoding="utf-8") as f:
        companies = json.load(f)

    by_id = {c.get("id"): c for c in companies if c.get("id")}
    by_name = {c.get("name", "").lower(): c for c in companies if c.get("name")}
    return by_id, by_name


def load_profile(profile_name: str):
    """
    Загружает компании из data/companies.json
    
    - profile="all" → все компании
    - profile="fintech" → компании с тегом "fintech"
    - profile="banking" → компании с тегом "bank" или "banking"
    """
    companies_path = DATA_DIR / "companies.json"
    
    if not companies_path.exists():
        print(f"⚠️ {companies_path} not found")
        return []
    
    with companies_path.open("r", encoding="utf-8") as f:
        all_companies = json.load(f)
    
    # Преобразуем в формат для парсеров
    def to_parser_format(c):
        return {
            "company": c.get("name", ""),
            "ats": c.get("ats", ""),
            "url": c.get("board_url", ""),
            "api_url": c.get("api_url"),
            "industry": c.get("industry", ""),
            "tags": c.get("tags", []),
            "priority": c.get("priority", 0),
            "hq_state": c.get("hq_state"),
            "region": c.get("region")
        }
    
    # Если "all" - возвращаем все компании
    if profile_name == "all":
        return [to_parser_format(c) for c in all_companies]
    
    # Иначе фильтруем по тегам
    tag_mappings = {
        "fintech": ["fintech", "payments", "banking", "crypto", "trading", "cards"],
        "banking": ["bank", "banking", "finserv"],
        "saas": ["saas", "enterprise"],
        "security": ["security", "infosec", "identity"],
    }
    
    search_tags = tag_mappings.get(profile_name, [profile_name])
    
    filtered = []
    for c in all_companies:
        company_tags = [t.lower() for t in c.get("tags", [])]
        if any(tag.lower() in company_tags for tag in search_tags):
            filtered.append(to_parser_format(c))
    
    print(f"✅ Profile '{profile_name}': {len(filtered)} companies (tags: {search_tags})")
    return filtered


def _load_json_list(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def _save_json_list(path: Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_status_map():
    rows = _load_json_list(STATUS_FILE)
    return {row["job_url"]: row for row in rows}


def update_job_status(job_url: str, status: str, company: str = "", title: str = ""):
    """
    status == "clear" -> очищаем статус
    """
    rows = _load_json_list(STATUS_FILE)
    now = datetime.utcnow().isoformat() + "Z"

    for row in rows:
        if row["job_url"] == job_url:
            row["status"] = "" if status == "clear" else status
            row["company"] = company or row.get("company", "")
            row["title"] = title or row.get("title", "")
            row["updated_at"] = now
            break
    else:
        rows.append(
            {
                "job_url": job_url,
                "status": "" if status == "clear" else status,
                "company": company,
                "title": title,
                "updated_at": now,
            }
        )

    _save_json_list(STATUS_FILE, rows)


def get_hide_set():
    rows = _load_json_list(HIDE_FILE)
    return {row["job_url"] for row in rows}


def hide_job(job_url: str, reason: str = "manual_hide"):
    rows = _load_json_list(HIDE_FILE)
    now = datetime.utcnow().isoformat() + "Z"
    if job_url not in {r["job_url"] for r in rows}:
        rows.append({"job_url": job_url, "reason": reason, "timestamp": now})
        _save_json_list(HIDE_FILE, rows)
