import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
PROFILES_DIR = BASE_DIR / "profiles"
DATA_DIR = BASE_DIR.parent / "data"
STATUS_FILE = BASE_DIR / "job_status.json"
HIDE_FILE = BASE_DIR / "job_hide.json"


def _ensure_profiles_dir():
    PROFILES_DIR.mkdir(exist_ok=True)


def load_profile(profile_name: str):
    """
    Загрузка профиля с поддержкой старого и нового форматов.
    Старый формат: { "companies": [ ... ] }
    Новый формат:
      { "company_ids": [ "id1", "id2" ] }
      или
      { "filter": { "include_tags": [...], "exclude_tags": [...], "min_priority": 0 } }

    Возвращает список компаний с обогащением из мастер-листа (ats, board_url и др.)
    """
    _ensure_profiles_dir()
    profile_path = PROFILES_DIR / f"{profile_name}.json"
    if not profile_path.exists():
        profile_path = PROFILES_DIR / "all.json"

    with profile_path.open("r", encoding="utf-8") as f:
        profile_data = json.load(f)

    by_id, by_name = load_companies_master()

    def enrich_company(c):
        """
        Подмешиваем в компанию данные из мастер-листа, если их нет.
        Ищем в by_id, иначе по имени lower()
        """
        if not c or not isinstance(c, dict):
            return c

        c_id = c.get("id") or c.get("company", "").lower().replace(" ", "")
        master = by_id.get(c_id)
        if not master and c.get("company"):
            master = by_name.get(c["company"].lower())

        if master:
            for key in ["ats", "board_url", "tags", "priority", "hq_state"]:
                if key not in c or c[key] in [None, ""]:
                    c[key] = master.get(key) if key in master else c.get(key)
            # Обновим id и name для унификации
            c["id"] = master.get("id", c.get("id"))
            c["name"] = master.get("name", c.get("company", c.get("name")))
        else:
            c["id"] = c_id
            c["name"] = c.get("company", c.get("name"))

        # Обеспечим поля tags и priority
        if "tags" not in c or not isinstance(c["tags"], list):
            c["tags"] = []
        if "priority" not in c or not isinstance(c["priority"], int):
            c["priority"] = 0

        return c

    # Новый формат
    if isinstance(profile_data, dict) and ("filter" in profile_data or "company_ids" in profile_data):
        companies = []
        if "company_ids" in profile_data:
            for cid in profile_data["company_ids"]:
                c = by_id.get(cid)
                if c:
                    companies.append(c)
        elif "filter" in profile_data:
            filt = profile_data["filter"]
            include_tags = set(filt.get("include_tags", []))
            exclude_tags = set(filt.get("exclude_tags", []))
            min_priority = filt.get("min_priority", 0)

            for c in by_id.values():
                tags = set(c.get("tags", []))
                priority = c.get("priority", 0)
                if include_tags and not include_tags.intersection(tags):
                    continue
                if exclude_tags and exclude_tags.intersection(tags):
                    continue
                if priority < min_priority:
                    continue
                companies.append(c)

    # Старый формат
    elif isinstance(profile_data, dict) and "companies" in profile_data:
        companies = [enrich_company(c) for c in profile_data["companies"]]
    elif isinstance(profile_data, list):
        companies = [enrich_company(c) for c in profile_data]
    else:
        companies = []

    return companies


def load_companies(profile_name: str = "all"):
    """
    Загружает мастер-список компаний из profiles/all.json
    и фильтрует по профилю (bigtech, fintech, core_product...).

    all.json: список объектов компаний.
    profile.json: список имён компаний.
    """
    _ensure_profiles_dir()
    all_path = PROFILES_DIR / "all.json"
    if not all_path.exists():
        return []

    with all_path.open("r", encoding="utf-8") as f:
        all_companies = json.load(f)

    if profile_name == "all":
        return all_companies

    profile_list_path = PROFILES_DIR / f"{profile_name}.json"
    if not profile_list_path.exists():
        return all_companies

    with profile_list_path.open("r", encoding="utf-8") as f:
        names = set(json.load(f))

    return [c for c in all_companies if c.get("company") in names]


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
    """
    Возвращает dict: job_url -> row (status, company, title, updated_at)
    """
    rows = _load_json_list(STATUS_FILE)
    return {row["job_url"]: row for row in rows}


def update_job_status(job_url: str, status: str, company: str = "", title: str = ""):
    """
    Добавляет/обновляет статус вакансии в job_status.json.
    Если status == "clear" — очищаем статус.
    """
    rows = _load_json_list(STATUS_FILE)
    now = datetime.utcnow().isoformat() + "Z"

    for row in rows:
        if row["job_url"] == job_url:
            if status == "clear":
                row["status"] = ""
            else:
                row["status"] = status
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
    """
    Возвращает set скрытых job_url
    """
    rows = _load_json_list(HIDE_FILE)
    return {row["job_url"] for row in rows}


def hide_job(job_url: str, reason: str = "manual_hide"):
    """
    Помечает вакансию как скрытую (job_hide.json)
    """
    rows = _load_json_list(HIDE_FILE)
    now = datetime.utcnow().isoformat() + "Z"
    if job_url not in {r["job_url"] for r in rows}:
        rows.append({"job_url": job_url, "reason": reason, "timestamp": now})
        _save_json_list(HIDE_FILE, rows)


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

