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
    Поддержка:
    - Старый формат: список компаний или { "companies": [ ... ] }
    - Новый формат:
        { "company_ids": [ ... ] }
        { "filter": { include_tags, exclude_tags, min_priority } }

    На выходе возвращаем НОРМАЛИЗОВАННЫЕ компании со ВСЕМИ нужными ключами:
      id, name, ats, board_url, tags, priority, hq_state
    И для backward-compat:
      company (=name), url (=board_url)
    """
    _ensure_profiles_dir()
    profile_path = PROFILES_DIR / f"{profile_name}.json"
    if not profile_path.exists():
        profile_path = PROFILES_DIR / "all.json"

    with profile_path.open("r", encoding="utf-8") as f:
        profile_data = json.load(f)

    by_id, by_name = load_companies_master()

    def normalize_company(c: dict) -> dict:
        if not c or not isinstance(c, dict):
            return {}

        # пытаемся найти в мастер-листе
        c_id = c.get("id")
        c_name = c.get("name") or c.get("company")

        master = None
        if c_id:
            master = by_id.get(c_id)
        if not master and c_name:
            master = by_name.get(str(c_name).lower())

        out = {}
        src = master or c

        out["id"] = (src.get("id") or c_id or (str(c_name).lower().replace(" ", "") if c_name else "")).strip()
        out["name"] = (src.get("name") or c_name or "").strip()
        out["ats"] = (src.get("ats") or c.get("ats") or "").strip().lower()
        out["board_url"] = (src.get("board_url") or src.get("url") or c.get("board_url") or c.get("url") or "").strip()
        out["tags"] = src.get("tags") if isinstance(src.get("tags"), list) else []
        out["priority"] = int(src.get("priority") or 0)
        out["hq_state"] = src.get("hq_state", None)
        out["industry"] = src.get("industry", c.get("industry", ""))

        # BACKWARD COMPAT для старого main/parsers
        out["company"] = out["name"]
        out["url"] = out["board_url"]

        return out

    companies: list[dict] = []

    # Новый формат профиля
    if isinstance(profile_data, dict) and ("filter" in profile_data or "company_ids" in profile_data):
        if "company_ids" in profile_data:
            for cid in profile_data.get("company_ids", []):
                m = by_id.get(cid)
                if m:
                    companies.append(normalize_company(m))
        elif "filter" in profile_data:
            filt = profile_data["filter"] or {}
            include_tags = set(filt.get("include_tags", []))
            exclude_tags = set(filt.get("exclude_tags", []))
            min_priority = int(filt.get("min_priority", 0))

            for m in by_id.values():
                tags = set(m.get("tags", [])) if isinstance(m.get("tags", []), list) else set()
                priority = int(m.get("priority") or 0)

                if include_tags and not include_tags.intersection(tags):
                    continue
                if exclude_tags and exclude_tags.intersection(tags):
                    continue
                if priority < min_priority:
                    continue

                companies.append(normalize_company(m))

    # Старые форматы
    elif isinstance(profile_data, dict) and "companies" in profile_data:
        companies = [normalize_company(c) for c in profile_data["companies"]]
    elif isinstance(profile_data, list):
        companies = [normalize_company(c) for c in profile_data]
    else:
        companies = []

    # выкинем совсем пустые
    companies = [c for c in companies if c.get("name") and c.get("ats") and c.get("board_url")]
    return companies


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
