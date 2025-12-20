from datetime import datetime, timezone
import json
from collections import Counter

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from parsers.greenhouse import fetch_greenhouse
from parsers.lever import fetch_lever
from parsers.smartrecruiters import fetch_smartrecruiters  # если нет – можно временно закомментить
from storage import load_profile
from utils.normalize import normalize_location, classify_role, STATE_MAP


# Кэш статуса по компаниям: "profile:company" -> {ok, error, checked_at, ats, url}
company_fetch_status: dict[str, dict] = {}

# Geo scoring/bucketing configuration
TARGET_STATE = "NC"
NEIGHBOR_STATES = {"VA", "SC", "GA", "TN"}
LOCAL_CITIES = {"raleigh", "durham", "cary", "chapel hill", "morrisville"}


def compute_geo_bucket_and_score(loc_norm: dict | None) -> tuple[str, int]:
    if not loc_norm:
        return "unknown", 0

    city = (loc_norm.get("city") or "").lower()
    state = (loc_norm.get("state") or "").upper() if loc_norm.get("state") else None
    remote = bool(loc_norm.get("remote"))
    remote_scope = (loc_norm.get("remote_scope") or "").lower()

    if city in LOCAL_CITIES and state == TARGET_STATE:
        return "local", 100
    if state == TARGET_STATE:
        return "nc", 80
    if state in NEIGHBOR_STATES:
        return "neighbor", 60
    if remote and remote_scope in ["usa", "us"]:
        return "remote_usa", 50
    if state or city or remote:
        return "other", 0
    return "unknown", 0


app = FastAPI(
    title="Job Tracker",
    description="Simple job aggregator for Product / PM roles from ATS",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


def _is_us_location(location: str | None) -> bool:
    if not location:
        return False
    loc = location.lower()
    if "united states" in loc or "usa" in loc or "us" in loc:
        return True
    # очень грубый хак по штатам
    us_markers = [
        ", ca", ", ny", ", wa", ", ma", ", tx", ", co", ", il", ", ga", ", nc",
        "washington, dc", "new york, ny", "san francisco", "remote - us",
    ]
    return any(m in loc for m in us_markers)


def _mark_company_status(profile: str, cfg: dict, ok: bool, error: str | None = None):
    key = f"{profile}:{cfg.get('company', '')}"
    company_fetch_status[key] = {
        "ok": ok,
        "error": error or "",
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "ats": cfg.get("ats", ""),
        "url": cfg.get("url", ""),
    }


def _fetch_for_company(profile: str, cfg: dict) -> list[dict]:
    """
    Унифицированный вызов парсеров + запись статуса компании.
    Также добавляет нормализованную локацию, классификацию роли, geo bucket/score, company_data.
    """
    company = cfg.get("company", "")
    ats = cfg.get("ats", "")
    url = cfg.get("url", "")

    try:
        if ats == "greenhouse":
            jobs = fetch_greenhouse(company, url)
        elif ats == "lever":
            jobs = fetch_lever(company, url)
        elif ats == "smartrecruiters":
            jobs = fetch_smartrecruiters(company, url)
        else:
            jobs = []

        # записываем успех
        _mark_company_status(profile, cfg, ok=True)

        # добавляем мета-инфу к каждой вакансии
        for j in jobs:
            j["company"] = company
            j["industry"] = cfg.get("industry", "")
            j["ats"] = ats

            # нормализация локации
            loc_norm = normalize_location(j.get("location"))
            j["location_norm"] = loc_norm

            # классификация роли
            role = classify_role(j.get("title"), j.get("description") or j.get("jd") or "")
            j["role_family"] = role.get("role_family")
            j["role_confidence"] = role.get("confidence")
            j["role_reason"] = role.get("reason")

            # company data (for scoring/prioritization)
            j["company_data"] = {
                "priority": cfg.get("priority", 0),
                "hq_state": cfg.get("hq_state", None),
                "region": cfg.get("region", None),
                "tags": cfg.get("tags", []),
            }

            # geo bucket + score
            bucket, score = compute_geo_bucket_and_score(loc_norm)
            j["geo_bucket"] = bucket
            j["geo_score"] = score

        return jobs

    except Exception as e:  # noqa: BLE001
        # фиксируем ошибку, но не валим весь /jobs
        _mark_company_status(profile, cfg, ok=False, error=str(e))
        print(f"Error for {company}: {e}")
        return []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/jobs")
async def get_jobs(
    profile: str = Query("all", description="Имя профиля из папки profiles/*.json"),
    ats_filter: str = Query("all", description="all / greenhouse / lever / smartrecruiters"),
    role_filter: str = Query("all", description="all / product / tpm_program / project / other"),
    location_filter: str = Query("all", description="all / us / nonus"),
    company_filter: str = Query("", description="подстрока в названии компании"),
    search: str = Query("", description="поиск по title+location"),
    states: str = Query("", description="Comma-separated US state codes or full names, e.g. NC,VA,South Carolina"),
    include_remote_usa: bool = Query(False, description="If true and states provided: include (state OR Remote-USA)"),
    state: str = Query("", description="(deprecated) Filter by state substring"),
    city: str = Query("", description="Filter by city substring"),
    geo_mode: str = Query("all", description="all / nc_priority / local_only / neighbor_only / remote_usa"),
):
    """
    Основной эндпоинт: собирает вакансии по профилю и фильтрам.
    """
    companies_cfg = load_profile(profile)
    all_jobs: list[dict] = []

    for cfg in companies_cfg:
        ats = cfg.get("ats", "")
        if ats_filter != "all" and ats_filter != ats:
            continue

        jobs = _fetch_for_company(profile, cfg)
        all_jobs.extend(jobs)

    # --- фильтры на уровне вакансий ---

    # parse states CSV to normalized list of 2-letter codes
    raw_states = [s.strip() for s in states.split(",") if s.strip()]
    normalized_states: list[str] = []
    for s in raw_states:
        s_low = s.lower()
        if s.upper() in STATE_MAP.values():
            normalized_states.append(s.upper())
        elif s_low in STATE_MAP:
            normalized_states.append(STATE_MAP[s_low])
        else:
            normalized_states.append(s.upper())

    states_set_upper = set(ns.upper() for ns in normalized_states)
    cities_set = set([city.lower()]) if city else set()

    def match_role(job: dict) -> bool:
        if role_filter == "all":
            return True
        return job.get("role_family") == role_filter

    def match_location(loc: str | None) -> bool:
        if location_filter == "all":
            return True
        is_us = _is_us_location(loc)
        if location_filter == "us":
            return is_us
        if location_filter == "nonus":
            return not is_us
        return True

    def match_company(name: str | None) -> bool:
        if not company_filter:
            return True
        if not name:
            return False
        return company_filter.lower() in name.lower()

    def match_search(job: dict) -> bool:
        if not search:
            return True
        s = search.lower()
        haystack = f"{job.get('title', '')} {job.get('location', '')}".lower()
        return s in haystack

    def match_states(job: dict) -> bool:
        loc_norm = job.get("location_norm", {}) or {}

        # Collect job states as 2-letter codes where possible
        job_states: list[str] = []
        if isinstance(loc_norm.get("states"), list):
            job_states.extend([str(st).upper() for st in (loc_norm.get("states") or []) if st])
        if loc_norm.get("state"):
            job_states.append(str(loc_norm.get("state")).upper())
        if loc_norm.get("state_full"):
            sf = str(loc_norm.get("state_full")).lower()
            if sf in STATE_MAP:
                job_states.append(STATE_MAP[sf])

        # Remote-USA flag
        remote_usa = bool(loc_norm.get("remote")) and (str(loc_norm.get("remote_scope") or "").lower() == "usa")

        # If no state filtering requested:
        if not normalized_states and not include_remote_usa:
            return True

        state_matches = any(ns in job_states for ns in states_set_upper)

        if include_remote_usa and normalized_states:
            return state_matches or remote_usa
        if include_remote_usa and not normalized_states:
            return remote_usa
        # normalized_states provided, include_remote_usa is False
        return state_matches

    def match_old_state(job: dict) -> bool:
        # fallback old state substring filter
        if not state:
            return True
        loc = job.get("location", "") or ""
        return state.lower() in loc.lower()

    def match_city(job: dict) -> bool:
        if not city:
            return True
        loc_norm = job.get("location_norm", {}) or {}
        if loc_norm:
            return city.lower() == str(loc_norm.get("city") or "").lower()
        loc = job.get("location", "") or ""
        return city.lower() in loc.lower()

    def match_geo(job: dict) -> bool:
        bucket = job.get("geo_bucket", "unknown")
        if geo_mode == "all":
            return True
        elif geo_mode == "nc_priority":
            return bucket in ["local", "nc", "neighbor", "remote_usa"]
        elif geo_mode == "local_only":
            return bucket == "local"
        elif geo_mode == "neighbor_only":
            return bucket == "neighbor"
        elif geo_mode == "remote_usa":
            return bucket == "remote_usa"
        return True

    filtered: list[dict] = []
    for j in all_jobs:
        if not match_role(j):
            continue
        if not match_states(j):
            continue
        if not match_old_state(j):
            continue
        if not match_city(j):
            continue
        if not match_geo(j):
            continue
        if not match_location(j.get("location")):
            continue
        if not match_company(j.get("company")):
            continue
        if not match_search(j):
            continue
        filtered.append(j)

    # compute score and sort
    def parse_date(datestr: str | None):
        if not datestr:
            return None
        try:
            ds = datestr.strip()
            # Convert trailing Z to explicit UTC offset for fromisoformat()
            if ds.endswith("Z"):
                ds = ds[:-1] + "+00:00"
            dt = datetime.fromisoformat(ds)
            # Ensure tz-aware in UTC. If naive, assume UTC.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            return None

    now = datetime.now(timezone.utc)
    for job in filtered:
        score = 0
        loc_norm = job.get("location_norm", {}) or {}

        job_state_upper = (loc_norm.get("state") or "").upper()
        job_city_lower = (loc_norm.get("city") or "").lower()
        job_remote_scope = (loc_norm.get("remote_scope") or "").lower()
        job_remote = bool(loc_norm.get("remote"))

        # Prefer explicit states/cities selections
        if states_set_upper and job_state_upper in states_set_upper:
            score += 30
        if cities_set and job_city_lower in cities_set:
            score += 15

        # If include_remote_usa requested, give a boost
        if include_remote_usa and job_remote_scope == "usa":
            score += 20
        # small credit for any remote if no states filtering but city preference
        if not states_set_upper and not city and job_remote:
            score += 5

        # Company priority
        company_data = job.get("company_data") or {}
        score += int(company_data.get("priority") or 0)

        # Freshness penalty
        updated = parse_date(job.get("updated_at"))
        if updated:
            age_days = (now - updated).days
            if age_days > 60:
                score -= 20
            elif age_days > 30:
                score -= 10

        # Add geo_score as primary weight
        score += int(job.get("geo_score", 0))

        job["score"] = score

    # сортировка по score, затем по updated_at
    filtered.sort(key=lambda j: (j.get("score", 0), str(j.get("updated_at") or "")), reverse=True)

    return {"count": len(filtered), "jobs": filtered}


@app.get("/companies")
def get_companies(
    profile: str = Query("all", description="Имя профиля из profiles/*.json"),
):
    """
    Возвращает список ВСЕХ компаний профиля + статус последней попытки fetch'а.
    """
    companies_cfg = load_profile(profile)
    items: list[dict] = []

    for cfg in companies_cfg:
        company_name = cfg.get("company", "")
        key = f"{profile}:{company_name}"
        st = company_fetch_status.get(key, {})

        items.append(
            {
                "company": company_name,
                "industry": cfg.get("industry", ""),
                "ats": cfg.get("ats", ""),
                "url": cfg.get("url", ""),
                "last_ok": st.get("ok", None),
                "last_error": st.get("error", ""),
                "last_checked": st.get("checked_at", ""),
            }
        )

    return {"count": len(items), "companies": items}


@app.get("/profiles/{name}")
async def get_profile_companies(name: str):
    companies = load_profile(name)
    result_companies = []
    for c in companies:
        result_companies.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "ats": c.get("ats", ""),
            "board_url": c.get("board_url", ""),
            "tags": c.get("tags", []),
            "priority": c.get("priority", 0),
            "hq_state": c.get("hq_state", None),
            "region": c.get("region", None)
        })
    return {"count": len(result_companies), "companies": result_companies}


@app.get("/debug/location_stats")
async def location_stats(profile: str = Query("all")):
    """
    Возвращает статистику по нормализованным локациям вакансий для указанного профиля.
    """
    companies_cfg = load_profile(profile)

    all_jobs: list[dict] = []
    # Собираем все вакансии аналогично /jobs через _fetch_for_company
    for cfg in companies_cfg:
        jobs = _fetch_for_company(profile, cfg)
        all_jobs.extend(jobs)

    total_jobs = len(all_jobs)
    remote_usa_count = 0
    remote_global_count = 0
    jobs_with_states_count = 0
    states_counter = Counter()

    for job in all_jobs:
        loc_norm = job.get("location_norm", {}) or {}
        if loc_norm.get("remote") and str(loc_norm.get("remote_scope")).lower() == "usa":
            remote_usa_count += 1
        if loc_norm.get("remote") and str(loc_norm.get("remote_scope")).lower() == "global":
            remote_global_count += 1

        states = loc_norm.get("states") or []
        if states:
            jobs_with_states_count += 1
            states_counter.update([str(s).upper() for s in states if s])

    top_20_states = states_counter.most_common(20)

    return {
        "total_jobs": total_jobs,
        "remote_usa_count": remote_usa_count,
        "remote_global_count": remote_global_count,
        "jobs_with_states_count": jobs_with_states_count,
        "top_20_states": top_20_states,
    }
