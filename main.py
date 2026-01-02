# I have access to - main.py

from __future__ import annotations

import os
from datetime import datetime, timezone
import json
from collections import Counter
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# Environment: PROD or DEV
ENV = os.getenv("JOB_TRACKER_ENV", "PROD")

from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from parsers.greenhouse import fetch_greenhouse
from parsers.lever import fetch_lever
from parsers.smartrecruiters import fetch_smartrecruiters
from parsers.ashby import fetch_ashby_jobs
from parsers.workday_v2 import fetch_workday_v2
from parsers.atlassian import fetch_atlassian
from ats_detector import try_repair_company, verify_ats_url
from company_storage import load_profile
from utils.normalize import normalize_location, STATE_MAP
from utils.cache_manager import load_cache, save_cache, clear_cache, get_cache_info, load_stats
from utils.job_utils import generate_job_id, classify_role, find_similar_jobs
from storage.job_storage import (
    get_all_jobs, get_jobs_by_status, get_jobs_by_statuses,
    get_active_jobs, get_archive_jobs, get_all_job_ids,
    add_job, add_jobs_bulk, update_status as job_update_status,
    update_last_seen, update_last_seen_bulk, mark_missing_jobs,
    get_stats as get_job_stats, get_job_by_id, job_exists,
    STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_OFFER,
    STATUS_REJECTED, STATUS_WITHDRAWN, STATUS_CLOSED, STATUS_EXCLUDED,
    ACTIVE_STATUSES, ARCHIVE_STATUSES,
)


# -----------------------------
# Runtime/local state files
# -----------------------------
JOB_STATUS_FILE = Path("job_status.json")

VALID_APPLICATION_STATUSES = ["New", "Applied", "Interview", "Offer", "Rejected", "Withdrawn", "Closed"]

# My Roles families for filtering
MY_ROLE_FAMILIES = {"product", "tpm_program", "project"}


def sync_cache_to_pipeline(jobs: list) -> dict:
    """
    Sync parsed jobs from cache to jobs.json (pipeline storage).
    Only adds jobs that match My Roles and are new.
    Returns stats: {added: N, updated: N, total: N}
    """
    # Filter to My Roles only
    my_roles_jobs = [
        j for j in jobs 
        if j.get("role_family") in MY_ROLE_FAMILIES
    ]
    
    # Get existing job IDs
    existing_ids = get_all_job_ids()
    
    # Separate new vs existing
    new_jobs = [j for j in my_roles_jobs if j.get("id") not in existing_ids]
    existing_jobs = [j for j in my_roles_jobs if j.get("id") in existing_ids]
    
    # Add new jobs in bulk
    added = add_jobs_bulk(new_jobs, STATUS_NEW) if new_jobs else 0
    
    # Update last_seen for existing jobs
    existing_job_ids = {j.get("id") for j in existing_jobs}
    updated = update_last_seen_bulk(existing_job_ids) if existing_job_ids else 0
    
    return {
        "added": added,
        "updated": updated,
        "total_my_roles": len(my_roles_jobs),
        "total_parsed": len(jobs)
    }


def _safe_read_json(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        txt = path.read_text(encoding="utf-8").strip()
        if not txt:
            return {}
        return json.loads(txt)
    except Exception:
        return {}


def _safe_write_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"Failed to write {path}: {e}")


def _load_job_status_map(profile: str) -> dict[str, str]:
    """
    Returns dict: job_key -> status
    Stored format:
      {
        "profiles": {
          "all": {"<job_key>":"Applied", ...},
          "fintech": {...}
        }
      }
    """
    root = _safe_read_json(JOB_STATUS_FILE)
    profiles = root.get("profiles") if isinstance(root, dict) else None
    if not isinstance(profiles, dict):
        return {}
    mp = profiles.get(profile)
    if isinstance(mp, dict):
        # ensure values are strings
        out: dict[str, str] = {}
        for k, v in mp.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k] = v
        return out
    return {}


def _set_job_status(profile: str, job_key: str, status: str) -> None:
    status_norm = status if status in VALID_APPLICATION_STATUSES else "New"
    root = _safe_read_json(JOB_STATUS_FILE)
    if not isinstance(root, dict):
        root = {}
    profiles = root.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}
        root["profiles"] = profiles
    mp = profiles.get(profile)
    if not isinstance(mp, dict):
        mp = {}
        profiles[profile] = mp
    mp[job_key] = status_norm
    _safe_write_json(JOB_STATUS_FILE, root)


def compute_job_key(job: dict) -> str:
    """
    Stable key for status tracking.
    Preference:
      1) job_url
      2) url
      3) (company|title|location)
    """
    url = (job.get("job_url") or job.get("url") or "").strip()
    if url:
        return url
    company = (job.get("company") or "").strip()
    title = (job.get("title") or "").strip()
    location = (job.get("location") or "").strip()
    return f"{company}|{title}|{location}".strip("|")


# Кэш статуса по компаниям: "profile:company" -> {ok, error, checked_at, ats, url}
COMPANY_STATUS_FILE = Path("data/company_status.json")

def load_company_status() -> dict:
    """Load company fetch status from file"""
    if COMPANY_STATUS_FILE.exists():
        try:
            with open(COMPANY_STATUS_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_company_status(status: dict):
    """Save company fetch status to file"""
    try:
        with open(COMPANY_STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        print(f"Failed to save company status: {e}")

# Load on startup
company_fetch_status: dict[str, dict] = load_company_status()

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
    version="0.3.0",
)

# Gzip compression for large responses (like 8MB jobs cache)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    """Redirect to UI"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/index.html")


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
    # Save to file for persistence
    save_company_status(company_fetch_status)


def _fetch_for_company(profile: str, cfg: dict, _retry: bool = False) -> list[dict]:
    """
    Унифицированный вызов парсеров + запись статуса компании.
    Также добавляет нормализованную локацию, классификацию роли, geo bucket/score, company_data.
    _retry: internal flag to prevent infinite recursion
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
            # SmartRecruiters needs api_url, not board_url
            api_url = cfg.get("api_url") or url
            jobs = fetch_smartrecruiters(company, api_url)
        elif ats == "ashby":
            jobs = fetch_ashby_jobs(url)
        elif ats == "workday":
            jobs = fetch_workday_v2(company, url)
        elif ats == "atlassian":
            jobs = fetch_atlassian(company, url)
        else:
            jobs = []

        # записываем успех
        _mark_company_status(profile, cfg, ok=True)

        # добавляем мета-инфу к каждой вакансии
        for j in jobs:
            j["company"] = company
            j["industry"] = cfg.get("industry", "")
            if not j.get("ats"):
                j["ats"] = ats

            # Генерируем уникальный ID (используя ats_job_id из парсера)
            j["id"] = generate_job_id(j)

            # нормализация локации
            loc_norm = normalize_location(j.get("location"))
            j["location_norm"] = loc_norm

            # классификация роли (улучшенная, с roles.json)
            role = classify_role(j.get("title"), j.get("description") or j.get("jd") or "")
            j["role_family"] = role.get("role_family")
            j["role_category"] = role.get("role_category")  # primary/adjacent/unknown/excluded
            j["role_id"] = role.get("role_id")
            j["role_confidence"] = role.get("confidence")
            j["role_reason"] = role.get("reason")
            j["role_excluded"] = role.get("excluded", False)
            j["role_exclude_reason"] = role.get("exclude_reason")

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
        error_str = str(e)
        print(f"Error for {company}: {error_str}")
        
        # Try auto-repair for 404 errors (only on first attempt, not retry)
        if "404" in error_str and not _retry:
            print(f"  🔧 Attempting auto-repair for {company}...")
            repair_result = try_repair_company({
                "name": company,
                "ats": ats,
                "board_url": url,
            })
            
            if repair_result and repair_result.get("verified"):
                new_ats = repair_result["ats"]
                new_url = repair_result["board_url"]
                print(f"  ✅ Found new URL: {new_ats} → {new_url}")
                
                # Update companies.json
                try:
                    companies_path = Path("data/companies.json")
                    with open(companies_path, "r") as f:
                        companies = json.load(f)
                    
                    for c in companies:
                        if c.get("name", "").lower() == company.lower():
                            c["ats"] = new_ats
                            c["board_url"] = new_url
                            print(f"  ✅ Updated companies.json for {company}")
                            break
                    
                    with open(companies_path, "w") as f:
                        json.dump(companies, f, indent=2, ensure_ascii=False)
                    
                    # Retry fetch with new URL (with _retry=True to prevent loop)
                    new_cfg = cfg.copy()
                    new_cfg["ats"] = new_ats
                    new_cfg["url"] = new_url
                    return _fetch_for_company(profile, new_cfg, _retry=True)
                    
                except Exception as update_err:
                    print(f"  ❌ Failed to update companies.json: {update_err}")
            else:
                print(f"  ❌ Auto-repair failed for {company}")
        
        # Mark as failed
        _mark_company_status(profile, cfg, ok=False, error=error_str)
        return []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/env")
def get_env():
    """Return current environment (PROD/DEV)"""
    return {"env": ENV}


class StatusUpdate(BaseModel):
    profile: str = "all"
    job_key: str
    status: str


@app.get("/job_status")
def get_job_status(profile: str = Query("all")):
    """
    Return status map for a profile:
      { "count": N, "statuses": { job_key: status } }
    """
    mp = _load_job_status_map(profile)
    return {"count": len(mp), "statuses": mp}


@app.post("/job_status")
def update_job_status(payload: StatusUpdate):
    """
    Update status for a job_key under a profile.
    """
    status = payload.status if payload.status in VALID_APPLICATION_STATUSES else "New"
    _set_job_status(payload.profile, payload.job_key, status)
    return {"ok": True, "profile": payload.profile, "job_key": payload.job_key, "status": status}


@app.get("/jobs")
async def get_jobs(
    profile: str = Query("all", description="Имя профиля из папки profiles/*.json"),
    ats_filter: str = Query("all", description="all / greenhouse / lever / smartrecruiters"),
    role_filter: str = Query("all", description="all / product / tpm_program / project / other"),
    location_filter: str = Query("all", description="all / us / nonus"),
    company_filter: str = Query("", description="подстрока в названии компании"),
    search: str = Query("", description="поиск по title+location"),
    states: str = Query("", description="Comma-separated US state codes or full names, e.g. NC,VA,South Carolina"),
    include_remote_usa: bool = Query(False, description="Include Remote-USA roles in addition to state selection"),
    state: str = Query("", description="(deprecated) Filter by state substring"),
    city: str = Query("", description="Filter by city substring"),
    geo_mode: str = Query("all", description="all / nc_priority / local_only / neighbor_only / remote_usa"),
    refresh: bool = Query(False, description="Force refresh from ATS, ignore cache"),
):
    """
    Основной эндпоинт: собирает вакансии по профилю и фильтрам.
    """
    # NEW: Check cache first (unless refresh=True)
    cache_key = profile
    cached = None if refresh else load_cache(cache_key)
    
    if cached:
        print(f"✅ Using cached data ({cached['jobs_count']} jobs)")
        all_jobs = cached["jobs"]
    else:
        # Parse from companies
        companies_cfg = load_profile(profile)
        all_jobs: list[dict] = []
        
        # Filter companies first
        companies_to_fetch = []
        for cfg in companies_cfg:
            if cfg.get("enabled") == False:
                continue
            ats = cfg.get("ats", "")
            if ats_filter != "all" and ats_filter != ats:
                continue
            companies_to_fetch.append(cfg)
        
        # Parallel fetch with ThreadPoolExecutor
        def fetch_company(cfg):
            return _fetch_for_company(profile, cfg)
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_company, cfg): cfg for cfg in companies_to_fetch}
            for future in as_completed(futures):
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                except Exception as e:
                    cfg = futures[future]
                    print(f"Error fetching {cfg.get('company', 'unknown')}: {e}")
        
        # NEW: Save to cache after parsing all companies
        save_cache(cache_key, all_jobs)
    
    # Load status map once
    status_map = _load_job_status_map(profile)

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
        haystack = f"{job.get('title', '')} {job.get('location', '')} {job.get('company', '')}".lower()
        return s in haystack

    def match_states(job: dict) -> bool:
        loc_norm = job.get("location_norm", {}) or {}
        # Collect job states as 2-letter codes where possible
        job_states = []
        if isinstance(loc_norm.get("states"), list):
            job_states.extend([str(st).upper() for st in loc_norm.get("states") if st])
        if loc_norm.get("state"):
            job_states.append(str(loc_norm.get("state")).upper())
        if loc_norm.get("state_full"):
            sf = str(loc_norm.get("state_full")).lower()
            if sf in STATE_MAP:
                job_states.append(STATE_MAP[sf])

        # Remote-USA flag
        remote_usa = bool(loc_norm.get("remote")) and (str(loc_norm.get("remote_scope") or "").lower() in ["usa", "us"])
        state_matches = any(ns in job_states for ns in states_set_upper)

        # New behavior: states selection and remote toggle are independent.
        if normalized_states and include_remote_usa:
            return state_matches or remote_usa
        if normalized_states and not include_remote_usa:
            return state_matches
        if not normalized_states and include_remote_usa:
            return remote_usa
        return True

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
            ds = datestr
            if ds.endswith("Z"):
                ds = ds[:-1] + "+00:00"
            dt = datetime.fromisoformat(ds)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
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

        # Attach job_key + status
        job_key = compute_job_key(job)
        job["job_key"] = job_key
        job["application_status"] = status_map.get(job_key, "New")

    filtered.sort(key=lambda j: (j.get("score", 0), str(j.get("updated_at") or "")), reverse=True)

    # ========== PIPELINE SYNC ==========
    # Sync relevant jobs with pipeline storage
    try:
        known_ids = get_all_job_ids()
        active_ids = set()
        
        for job in filtered:
            job_id = job.get("id")
            if not job_id:
                continue
            
            active_ids.add(job_id)
            
            # Only sync jobs with relevant roles
            role_family = job.get("role_family", "other")
            if role_family not in ["product", "tpm_program", "project"]:
                continue
            
            # Skip if role was excluded
            if job.get("role_excluded"):
                continue
            
            if job_id in known_ids:
                # Already known - update last_seen
                update_last_seen(job_id, is_active=True)
            else:
                # New job - add to inbox
                add_job(job)
        
        # Mark missing jobs as potentially closed
        mark_missing_jobs(active_ids, days_threshold=3)
        
    except Exception as e:
        print(f"Pipeline sync error: {e}")
    # ========== END PIPELINE SYNC ==========

    return {"count": len(filtered), "jobs": filtered}


@app.get("/companies")
def get_companies(
    profile: str = Query("all", description="Имя профиля из profiles/*.json"),
    my_roles: bool = Query(False, description="Filter by My Roles (Product, TPM, Program)"),
    my_location: bool = Query(False, description="Filter by My Location (US + Remote USA + NC,VA,SC,GA,TN)"),
):
    """
    Возвращает список ВСЕХ компаний профиля + статус последней попытки fetch'а + статистика jobs.
    """
    companies_cfg = load_profile(profile)
    
    # My Roles filter
    MY_ROLES = ["product", "tpm_program", "project"]
    
    # My Location filter states
    MY_LOCATION_STATES = ["NC", "VA", "SC", "GA", "TN"]
    
    # Load all jobs from pipeline for counting
    all_pipeline_jobs = get_active_jobs()
    
    # Load cache to get total jobs per company (all jobs from ATS)
    cache_data = load_cache(profile)  # Cache key is just 'all', not 'jobs_all'
    cache_jobs = cache_data.get("jobs", []) if cache_data else []
    
    # Build total_jobs per company from cache
    total_jobs_by_company = {}
    for job in cache_jobs:
        company_name = job.get("company", "")
        total_jobs_by_company[company_name] = total_jobs_by_company.get(company_name, 0) + 1
    
    # Filter jobs by role if my_roles is enabled
    def job_matches_my_roles(job):
        if not my_roles:
            return True
        role_family = job.get("role_family", "other")
        return role_family in MY_ROLES
    
    # Filter jobs by geo if my_location is enabled
    def job_matches_my_location(job):
        if not my_location:
            return True
        
        loc_norm = job.get("location_norm", {})
        state = loc_norm.get("state", "")
        remote = loc_norm.get("remote", False)
        remote_scope = loc_norm.get("remote_scope", "")
        
        # Check if Remote USA
        if remote and remote_scope == "usa":
            return True
        
        # Check if in My Location states
        if state in MY_LOCATION_STATES:
            return True
        
        return False
    
    filtered_jobs = [j for j in all_pipeline_jobs if job_matches_my_roles(j) and job_matches_my_location(j)]
    
    # Build company stats: company_name -> {jobs_count, applied_count, new_count, status_counts}
    company_stats = {}
    for job in filtered_jobs:
        company_name = job.get("company", "")
        if company_name not in company_stats:
            company_stats[company_name] = {
                "jobs_count": 0,
                "new_count": 0,
                "applied_count": 0,
                "interview_count": 0,
            }
        
        stats = company_stats[company_name]
        stats["jobs_count"] += 1
        
        status = job.get("status", "New")
        if status == STATUS_NEW:
            stats["new_count"] += 1
        elif status == STATUS_APPLIED:
            stats["applied_count"] += 1
        elif status == STATUS_INTERVIEW:
            stats["interview_count"] += 1
    
    items: list[dict] = []

    for cfg in companies_cfg:
        company_name = cfg.get("company", "") or cfg.get("name", "")
        key = f"{profile}:{company_name}"
        st = company_fetch_status.get(key, {})
        
        # For disabled companies, override status
        is_disabled = cfg.get("enabled") == False
        company_status = cfg.get("status", "active")
        
        # Get stats for this company
        stats = company_stats.get(company_name, {
            "jobs_count": 0,
            "new_count": 0, 
            "applied_count": 0,
            "interview_count": 0,
        })

        items.append(
            {
                "company": company_name,
                "id": cfg.get("id", ""),
                "industry": cfg.get("industry", ""),
                "tags": cfg.get("tags", []),
                "ats": cfg.get("ats", ""),
                "url": cfg.get("url", "") or cfg.get("board_url", ""),
                "enabled": cfg.get("enabled", True),
                "status": company_status,
                "last_ok": "disabled" if is_disabled else st.get("ok", None),
                "last_error": cfg.get("status", "") if is_disabled else st.get("error", ""),
                "last_checked": st.get("checked_at", ""),
                # Total jobs from cache (all jobs from ATS)
                "total_jobs": total_jobs_by_company.get(company_name, 0),
                # Stats from pipeline (filtered PM/TPM jobs)
                "jobs_count": stats["jobs_count"],
                "new_count": stats["new_count"],
                "applied_count": stats["applied_count"],
                "interview_count": stats["interview_count"],
            }
        )

    # Sort by total_jobs desc, then by name
    items.sort(key=lambda x: (-x["total_jobs"], x["company"].lower()))
    
    # Summary stats
    total_jobs = sum(c["jobs_count"] for c in items)
    total_new = sum(c["new_count"] for c in items)
    total_applied = sum(c["applied_count"] for c in items)
    total_interview = sum(c["interview_count"] for c in items)

    return {
        "count": len(items), 
        "companies": items,
        "summary": {
            "total_jobs": total_jobs,
            "total_new": total_new,
            "total_applied": total_applied,
            "total_interview": total_interview,
        }
    }


class CompanyCreate(BaseModel):
    name: str
    ats: str  # greenhouse, lever, smartrecruiters
    board_url: str
    industry: str = ""
    tags: list[str] = []


@app.post("/companies")
def add_company(company: CompanyCreate):
    """
    Add a new company to companies.json.
    """
    companies_path = Path("data/companies.json")
    
    # Load existing
    if companies_path.exists():
        with open(companies_path, "r") as f:
            companies = json.load(f)
    else:
        companies = []
    
    # Generate id from name
    company_id = company.name.lower().replace(" ", "_").replace("-", "_")
    
    # Check if already exists
    for c in companies:
        if c.get("id") == company_id or c.get("name", "").lower() == company.name.lower():
            return {"error": f"Company '{company.name}' already exists", "status": "exists"}
    
    # Create new company entry
    new_company = {
        "id": company_id,
        "name": company.name,
        "ats": company.ats,
        "board_url": company.board_url,
        "api_url": None,
        "tags": company.tags,
        "industry": company.industry,
        "priority": 0,
        "hq_state": None,
        "region": "us"
    }
    
    companies.append(new_company)
    
    # Save
    with open(companies_path, "w") as f:
        json.dump(companies, f, indent=2)
    
    return {"status": "ok", "company": new_company}


@app.delete("/companies/{company_id}")
def remove_company(company_id: str):
    """
    Remove a company from companies.json.
    """
    companies_path = Path("data/companies.json")
    
    if not companies_path.exists():
        return {"error": "No companies file", "status": "error"}
    
    with open(companies_path, "r") as f:
        companies = json.load(f)
    
    # Find and remove
    original_len = len(companies)
    companies = [c for c in companies if c.get("id") != company_id]
    
    if len(companies) == original_len:
        return {"error": f"Company '{company_id}' not found", "status": "not_found"}
    
    # Save
    with open(companies_path, "w") as f:
        json.dump(companies, f, indent=2)
    
    return {"status": "ok", "removed": company_id}

@app.post("/companies/{company_id}/refresh")
def refresh_single_company(company_id: str, profile: str = Query("all")):
    """
    Refresh jobs for a single company.
    Parses ATS, updates cache and pipeline.
    """
    # Load company config
    companies_cfg = load_profile(profile)
    cfg = None
    for c in companies_cfg:
        # Match by id, company name, or name field
        cid = c.get("id", "") or ""
        cname = c.get("company", "") or c.get("name", "") or ""
        if cid == company_id or cname.lower() == company_id.lower():
            cfg = c
            break
    
    if not cfg:
        return {"ok": False, "error": f"Company '{company_id}' not found"}
    
    company_name = cfg.get("company", "")
    
    # Fetch jobs for this company
    try:
        jobs = _fetch_for_company(profile, cfg)
        
        # Update cache - load existing, replace this company's jobs, save
        cached = load_cache(profile) or {"jobs": []}
        other_jobs = [j for j in cached.get("jobs", []) if j.get("company") != company_name]
        all_jobs = other_jobs + jobs
        save_cache(profile, all_jobs)
        
        # Sync to pipeline (add new My Roles jobs)
        sync_result = sync_cache_to_pipeline(jobs)
        
        return {
            "ok": True,
            "company": company_name,
            "jobs_count": len(jobs),
            "total_cache": len(all_jobs),
            "pipeline_added": sync_result["added"],
            "pipeline_updated": sync_result["updated"]
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/companies/{company_id}/refresh/stream")
async def refresh_single_company_stream(company_id: str, profile: str = Query("all")):
    """
    Streaming refresh for a single company.
    Sends progress events as jobs are parsed.
    """
    import asyncio
    
    async def generate():
        # Find company config
        companies_cfg = load_profile(profile)
        cfg = None
        for c in companies_cfg:
            cid = c.get("id", "") or ""
            cname = c.get("company", "") or c.get("name", "") or ""
            if cid == company_id or cname.lower() == company_id.lower():
                cfg = c
                break
        
        if not cfg:
            yield f"data: {json.dumps({'type': 'error', 'error': f'Company {company_id} not found'})}\n\n"
            return
        
        company_name = cfg.get("company", "") or cfg.get("name", "")
        ats = cfg.get("ats", "")
        url = cfg.get("url", "") or cfg.get("board_url", "")
        
        yield f"data: {json.dumps({'type': 'start', 'company': company_name, 'ats': ats})}\n\n"
        
        try:
            if ats == "workday":
                # Workday: stream progress page by page
                from parsers.workday_v2 import fetch_workday_v2_streaming
                
                raw_jobs = []
                for event in fetch_workday_v2_streaming(company_name, url):
                    if event.get("type") == "progress":
                        yield f"data: {json.dumps({'type': 'progress', 'jobs': event['jobs']})}\n\n"
                    elif event.get("type") == "done":
                        raw_jobs = event.get("jobs", [])
                    elif event.get("type") == "error":
                        yield f"data: {json.dumps({'type': 'error', 'error': event['error']})}\n\n"
                        return
                
                # Enrich jobs (same as _fetch_for_company)
                jobs = []
                for j in raw_jobs:
                    j["company"] = company_name
                    j["industry"] = cfg.get("industry", "")
                    if not j.get("ats"):
                        j["ats"] = ats
                    j["id"] = generate_job_id(j)
                    loc_norm = normalize_location(j.get("location"))
                    j["location_norm"] = loc_norm
                    role = classify_role(j.get("title"), j.get("description") or "")
                    j["role_family"] = role.get("role_family")
                    j["role_category"] = role.get("role_category")
                    j["role_id"] = role.get("role_id")
                    j["role_confidence"] = role.get("confidence")
                    j["role_reason"] = role.get("reason")
                    j["role_excluded"] = role.get("excluded", False)
                    j["role_exclude_reason"] = role.get("exclude_reason")
                    j["company_data"] = {
                        "priority": cfg.get("priority", 0),
                        "hq_state": cfg.get("hq_state"),
                        "region": cfg.get("region"),
                        "tags": cfg.get("tags", []),
                    }
                    bucket, score = compute_geo_bucket_and_score(loc_norm)
                    j["geo_bucket"] = bucket
                    j["geo_score"] = score
                    jobs.append(j)
                
                # Mark company status
                _mark_company_status(profile, cfg, ok=True)
            else:
                # Other ATS: single fetch
                yield f"data: {json.dumps({'type': 'progress', 'jobs': 0, 'message': 'Fetching...'})}\n\n"
                loop = asyncio.get_event_loop()
                jobs = await loop.run_in_executor(None, lambda: _fetch_for_company(profile, cfg))
            
            jobs_count = len(jobs)
            
            # Update cache
            cached = load_cache(profile) or {"jobs": []}
            other_jobs = [j for j in cached.get("jobs", []) if j.get("company") != company_name]
            all_jobs = other_jobs + jobs
            save_cache(profile, all_jobs)
            
            yield f"data: {json.dumps({'type': 'done', 'jobs': jobs_count, 'total_cache': len(all_jobs)})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)[:200]})}\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


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


# ============= NEW CACHE ENDPOINTS =============

@app.get("/cache/info")
def cache_info_endpoint(cache_key: str = Query("all")):
    """Get cache information"""
    info = get_cache_info(cache_key)
    return info


@app.post("/cache/refresh")
def cache_refresh_endpoint(cache_key: str = Query("all")):
    """Force refresh cache"""
    clear_cache(cache_key)
    return {"ok": True, "message": f"Cache cleared for '{cache_key}'. Next /jobs request will refresh."}


@app.delete("/cache/clear")
def cache_clear_all_endpoint():
    """Clear all caches"""
    clear_cache()
    return {"ok": True, "message": "All caches cleared"}


@app.get("/refresh/stream")
async def refresh_stream(profile: str = Query("all")):
    """
    Two-wave streaming refresh:
    Wave 1: Fast ATS (greenhouse, lever, ashby, smartrecruiters) - quick results
    Wave 2: Slow ATS (workday) - parallel in background
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    FAST_ATS = {"greenhouse", "lever", "ashby", "smartrecruiters"}
    SLOW_ATS = {"workday"}
    
    async def generate():
        companies_cfg = load_profile(profile)
        companies_cfg = [c for c in companies_cfg if c.get("enabled", True) != False]
        
        # Split into waves
        wave1 = [c for c in companies_cfg if c.get("ats", "") in FAST_ATS]
        wave2 = [c for c in companies_cfg if c.get("ats", "") in SLOW_ATS]
        
        total = len(companies_cfg)
        all_jobs = []
        idx = 0
        
        # Send start event
        yield f"data: {json.dumps({'type': 'start', 'total': total, 'wave1': len(wave1), 'wave2': len(wave2)})}\n\n"
        
        # === WAVE 1: Fast ATS (sequential, quick) ===
        yield f"data: {json.dumps({'type': 'wave', 'wave': 1, 'message': 'Fast ATS (Greenhouse, Lever, Ashby)'})}\n\n"
        
        for cfg in wave1:
            company_name = cfg.get("company", "") or cfg.get("name", "")
            yield f"data: {json.dumps({'type': 'loading', 'company': company_name, 'index': idx, 'total': total})}\n\n"
            
            try:
                jobs = _fetch_for_company(profile, cfg)
                all_jobs.extend(jobs)
                yield f"data: {json.dumps({'type': 'ok', 'company': company_name, 'jobs': len(jobs), 'index': idx, 'total': total})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'company': company_name, 'error': str(e)[:100], 'index': idx, 'total': total})}\n\n"
            
            idx += 1
            await asyncio.sleep(0.01)
        
        # Save intermediate cache (Wave 1 complete)
        save_cache(profile, all_jobs)
        
        # Sync wave 1 to pipeline
        sync_result = sync_cache_to_pipeline(all_jobs)
        yield f"data: {json.dumps({'type': 'wave_complete', 'wave': 1, 'jobs': len(all_jobs), 'pipeline_added': sync_result['added']})}"
        yield "\n\n"
        
        # === WAVE 2: Slow ATS (parallel) ===
        if wave2:
            yield f"data: {json.dumps({'type': 'wave', 'wave': 2, 'message': 'Slow ATS (Workday) - parallel'})}\n\n"
            
            def fetch_slow(cfg):
                return cfg, _fetch_for_company(profile, cfg)
            
            # Process in parallel with ThreadPoolExecutor
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(fetch_slow, cfg): cfg for cfg in wave2}
                
                for future in as_completed(futures):
                    cfg = futures[future]
                    company_name = cfg.get("company", "") or cfg.get("name", "")
                    
                    try:
                        _, jobs = future.result()
                        all_jobs.extend(jobs)
                        yield f"data: {json.dumps({'type': 'ok', 'company': company_name, 'jobs': len(jobs), 'index': idx, 'total': total})}\n\n"
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'company': company_name, 'error': str(e)[:100], 'index': idx, 'total': total})}\n\n"
                    
                    idx += 1
            
            # Save final cache
            save_cache(profile, all_jobs)
        
        # Save stats
        from utils.cache_manager import save_stats
        role_jobs = [j for j in all_jobs if j.get("role_family") in ["product", "tpm_program", "project"]]
        us_jobs = [j for j in role_jobs if j.get("location_norm", {}).get("state") or j.get("location_norm", {}).get("remote")]
        my_area_jobs = [j for j in us_jobs if j.get("geo_bucket") in ["local", "nc_other", "neighbor", "remote_usa"]]
        save_stats(len(all_jobs), len(role_jobs), len(us_jobs), len(my_area_jobs))
        
        # Sync to pipeline (jobs.json)
        sync_result = sync_cache_to_pipeline(all_jobs)
        yield f"data: {json.dumps({'type': 'sync', 'added': sync_result['added'], 'updated': sync_result['updated']})}"
        yield "\n\n"
        
        # Send complete event
        yield f"data: {json.dumps({'type': 'complete', 'total_jobs': len(all_jobs), 'companies': total, 'pipeline_added': sync_result['added']})}"
        yield "\n\n"
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.get("/stats")
def get_funnel_stats():
    """Get funnel stats (Total -> Role -> US -> My Area) from cache."""
    stats = load_stats()
    if stats:
        return stats
    else:
        return {
            "total": 0,
            "role": 0,
            "us": 0,
            "my_area": 0,
            "updated_at": None,
            "message": "Stats not computed yet. Run /jobs?refresh=true first."
        }



@app.get("/stats/by-date")
def get_stats_by_date(days: int = Query(14, ge=1, le=60)):
    """Get job statistics grouped by date with breakdown by category and location."""
    from collections import defaultdict
    from datetime import datetime, timedelta
    
    # Load from cache
    cached = load_cache("all")
    if not cached:
        return {"error": "Cache not loaded", "dates": []}
    
    all_jobs = cached.get("jobs", [])
    
    # Group by date
    by_date = defaultdict(lambda: {
        "total": 0,
        "primary": 0,
        "adjacent": 0,
        "unknown": 0,
        "excluded": 0,
        "us": 0,
        "remote": 0,
        "nc": 0,
        "neighbor": 0,
        "nonus": 0
    })
    
    neighbor_states = {"VA", "SC", "GA", "TN"}
    
    for job in all_jobs:
        # Get date
        updated = job.get("updated_at", "")
        if isinstance(updated, int):
            # Unix timestamp
            # Handle milliseconds
            if updated > 10000000000:
                updated = updated / 1000
            date_str = datetime.fromtimestamp(updated).strftime("%Y-%m-%d")
        elif updated:
            date_str = str(updated)[:10]
        else:
            date_str = "unknown"
        
        stats = by_date[date_str]
        stats["total"] += 1
        
        # Category
        cat = job.get("role_category", "unknown")
        if cat in stats:
            stats[cat] += 1
        
        # Location
        ln = job.get("location_norm", {}) or {}
        state = (ln.get("state") or "").upper()
        is_remote = ln.get("remote", False)
        
        if is_remote:
            stats["remote"] += 1
            stats["us"] += 1  # Remote USA counts as US
        elif state == "NC":
            stats["nc"] += 1
            stats["us"] += 1
        elif state in neighbor_states:
            stats["neighbor"] += 1
            stats["us"] += 1
        elif state:
            stats["us"] += 1
        elif job.get("location"):
            stats["nonus"] += 1
    
    # Sort by date descending, limit to days
    sorted_dates = sorted(by_date.items(), key=lambda x: x[0], reverse=True)
    
    # Filter to recent days only
    result = []
    for date_str, stats in sorted_dates[:days]:
        if date_str == "unknown":
            continue
        result.append({
            "date": date_str,
            **stats
        })
    
    return {
        "dates": result,
        "last_refresh": cached.get("last_updated")
    }

# ============= PIPELINE ENDPOINTS =============


@app.get("/cache/browse")
def browse_cache_jobs(
    date: str = Query(None, description="Filter by date (YYYY-MM-DD)"),
    category: str = Query(None, description="Filter by role_category"),
    location: str = Query(None, description="Filter by location (us/nc/neighbor/remote)"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=10, le=500)
):
    """Browse ALL cached jobs with filters (not just pipeline)"""
    from datetime import datetime
    
    cached = load_cache("all")
    if not cached:
        return {"error": "Cache not loaded", "jobs": [], "total": 0}
    
    all_jobs = cached.get("jobs", [])
    neighbor_states = {"VA", "SC", "GA", "TN"}
    
    # Apply date filter
    if date:
        def match_date(j):
            updated = j.get("updated_at", "")
            if isinstance(updated, int):
                if updated > 10000000000:
                    updated = updated / 1000
                return datetime.fromtimestamp(updated).strftime("%Y-%m-%d") == date
            return str(updated)[:10] == date
        all_jobs = [j for j in all_jobs if match_date(j)]
    
    # Apply category filter
    if category:
        all_jobs = [j for j in all_jobs if j.get("role_category") == category]
    
    # Apply location filter
    if location:
        filtered = []
        for j in all_jobs:
            ln = j.get("location_norm", {}) or {}
            state = (ln.get("state") or "").upper()
            is_remote = ln.get("remote", False)
            
            if location == "us" and (state or is_remote):
                filtered.append(j)
            elif location == "nc" and state == "NC":
                filtered.append(j)
            elif location == "neighbor" and state in neighbor_states:
                filtered.append(j)
            elif location == "remote" and is_remote:
                filtered.append(j)
        all_jobs = filtered
    
    # Check which are in pipeline
    pipeline_ids = get_all_job_ids()
    for j in all_jobs:
        j["in_pipeline"] = j.get("id") in pipeline_ids
    
    # Pagination
    total = len(all_jobs)
    start = (page - 1) * limit
    end = start + limit
    
    return {
        "jobs": all_jobs[start:end],
        "total": total,
        "page": page,
        "has_prev": page > 1,
        "has_next": end < total
    }

@app.get("/pipeline/stats")
def pipeline_stats_endpoint():
    """Get pipeline statistics"""
    return get_job_stats()


@app.get("/jobs/review")
def get_review_jobs(
    date: str = Query(None, description="Filter by date (YYYY-MM-DD)"),
    category: str = Query("unknown", description="unknown / excluded / all"),
    search: str = Query("", description="Search in title, company, location"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(100, ge=10, le=500, description="Jobs per page"),
):
    """
    Get Unknown + Excluded jobs with server-side pagination.
    Much faster than loading all 7500 jobs.
    """
    # Load from cache
    cache_key = "all"
    cached = load_cache(cache_key)
    
    if not cached:
        return {"error": "Cache not loaded. Run /jobs?refresh=true first.", "jobs": [], "total": 0}
    
    all_jobs = cached.get("jobs", [])

    # Apply date filter
    if date:
        all_jobs = [j for j in all_jobs if str(j.get("updated_at", ""))[:10] == date]
    
    # Filter by role_category (unknown or excluded)
    def get_category(job):
        if job.get("role_category"):
            return job["role_category"]
        if job.get("role_excluded"):
            return "excluded"
        if job.get("role_id"):
            return "primary"
        return "unknown"
    
    if category == "all":
        filtered = [j for j in all_jobs if get_category(j) in ["unknown", "excluded"]]
    else:
        filtered = [j for j in all_jobs if get_category(j) == category]
    
    # Filter by search
    if search:
        search_lower = search.lower()
        filtered = [
            j for j in filtered
            if search_lower in (j.get("title", "") + " " + j.get("company", "") + " " + j.get("location", "")).lower()
        ]
    
    # Pagination
    total = len(filtered)
    total_pages = (total + limit - 1) // limit  # ceiling division
    start = (page - 1) * limit
    end = start + limit
    page_jobs = filtered[start:end]
    
    # Check which jobs are already in pipeline
    pipeline_ids = get_all_job_ids()
    for job in page_jobs:
        job["in_pipeline"] = job.get("id") in pipeline_ids
    
    return {
        "jobs": page_jobs,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


@app.get("/pipeline/all")
def pipeline_all_endpoint(
    date: str = Query(None, description="Filter by date (YYYY-MM-DD)"),
    category: str = Query(None, description="Filter by role_category (primary/adjacent)"),
    location: str = Query(None, description="Filter by location (us/nc/neighbor/remote)")
):
    """Get ALL jobs from storage with optional filters"""
    all_jobs = get_all_jobs()
    
    # Apply date filter
    if date:
        all_jobs = [j for j in all_jobs if str(j.get("updated_at", ""))[:10] == date]
    
    # Apply category filter
    if category:
        all_jobs = [j for j in all_jobs if j.get("role_category") == category]
    
    # Apply location filter
    if location:
        neighbor_states = {"VA", "SC", "GA", "TN"}
        filtered = []
        for j in all_jobs:
            ln = j.get("location_norm", {}) or {}
            state = (ln.get("state") or "").upper()
            is_remote = ln.get("remote", False)
            
            if location == "us" and (state or is_remote):
                filtered.append(j)
            elif location == "nc" and state == "NC":
                filtered.append(j)
            elif location == "neighbor" and state in neighbor_states:
                filtered.append(j)
            elif location == "remote" and is_remote:
                filtered.append(j)
        all_jobs = filtered
    
    stats = get_job_stats()
    return {
        "count": len(all_jobs), 
        "jobs": all_jobs,
        "breakdown": stats["status_breakdown"]
    }



@app.get("/pipeline/new")
def pipeline_new_endpoint():
    """Get new (inbox) jobs"""
    jobs = get_jobs_by_status(STATUS_NEW)
    return {"count": len(jobs), "jobs": jobs}


@app.get("/pipeline/active")
def pipeline_active_endpoint():
    """Get active pipeline jobs (Applied, Interview)"""
    jobs = get_jobs_by_statuses({STATUS_APPLIED, STATUS_INTERVIEW, STATUS_CLOSED})
    return {"count": len(jobs), "jobs": jobs}


@app.get("/pipeline/archive")
def pipeline_archive_endpoint():
    """Get archived jobs (Rejected, Offer, Withdrawn)"""
    jobs = get_archive_jobs()
    return {"count": len(jobs), "jobs": jobs}


class PipelineAddJob(BaseModel):
    job: dict


@app.post("/pipeline/add")
def pipeline_add_job_endpoint(payload: PipelineAddJob):
    """
    Manually add a job to pipeline (for Unknown/Excluded jobs).
    """
    job = payload.job.copy()  # Don't modify original
    job_id = job.get("id")
    
    if not job_id:
        return {"ok": False, "error": "Job must have an id"}
    
    # Check if already in pipeline
    existing = get_job_by_id(job_id)
    if existing:
        return {"ok": False, "error": "Job already in pipeline"}
    
    # Mark as manually added
    job["source"] = "manual"
    
    # Add to pipeline
    added = add_job(job)
    
    if added:
        return {"ok": True, "job": job}
    else:
        return {"ok": False, "error": "Job already exists"}


@app.delete("/pipeline/remove/{job_id}")
def pipeline_remove_job_endpoint(job_id: str):
    """
    Remove a job from pipeline (for manual jobs).
    Only removes jobs with source='manual'.
    """
    job = get_job_by_id(job_id)
    
    if not job:
        return {"ok": False, "error": "Job not found"}
    
    # Only allow removing manual jobs
    if job.get("source") != "manual":
        return {"ok": False, "error": "Can only remove manually added jobs"}
    
    # Remove from jobs_new.json
    try:
        jobs_new_path = Path("data/jobs_new.json")
        with open(jobs_new_path, "r") as f:
            jobs = json.load(f)
        
        original_len = len(jobs)
        jobs = [j for j in jobs if j.get("id") != job_id]
        
        if len(jobs) == original_len:
            return {"ok": False, "error": "Job not found in storage"}
        
        with open(jobs_new_path, "w") as f:
            json.dump(jobs, f, indent=2)
        
        return {"ok": True, "removed": job_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class PipelineStatusUpdate(BaseModel):
    job_id: str
    status: str
    notes: str = ""


@app.post("/pipeline/status")
def pipeline_status_update_endpoint(payload: PipelineStatusUpdate):
    """
    Update job status in pipeline.
    Valid statuses: New, Selected, Ready, Applied, Interview, Offer, Rejected, Withdrawn, Closed
    """
    valid_statuses = [STATUS_NEW, "Selected", "Ready", STATUS_APPLIED, STATUS_INTERVIEW, 
                      STATUS_OFFER, STATUS_REJECTED, STATUS_WITHDRAWN, STATUS_CLOSED]
    
    if payload.status not in valid_statuses:
        return {"ok": False, "error": f"Invalid status. Valid: {valid_statuses}"}
    
    job = job_update_status(payload.job_id, payload.status, payload.notes)
    
    if job:
        return {"ok": True, "job": job}
    else:
        return {"ok": False, "error": "Job not found"}


@app.get("/pipeline/job/{job_id}")
def pipeline_get_job_endpoint(job_id: str):
    """Get job by ID from any storage"""
    job = get_job_by_id(job_id)
    if job:
        return {"ok": True, "job": job}
    else:
        return {"ok": False, "error": "Job not found"}


@app.get("/pipeline/attention")
def pipeline_attention_endpoint():
    """Get jobs that need attention (Closed, etc.)"""
    all_jobs = get_all_jobs()
    attention = [j for j in all_jobs if j.get("needs_attention")]
    return {"count": len(attention), "jobs": attention}


# ============= SYNC DEV->PROD ENDPOINT =============

@app.post("/sync-to-prod")
def sync_to_prod_endpoint():
    """
    Sync data from DEV to PROD.
    Only available in DEV environment.
    """
    if ENV != "DEV":
        return {"ok": False, "error": "Only available in DEV environment"}
    
    try:
        from sync_to_prod import sync_companies, sync_jobs
        
        companies_result = sync_companies()
        jobs_result = sync_jobs()
        
        return {
            "ok": True,
            "companies": companies_result,
            "jobs": jobs_result,
            "synced_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ============= ONBOARDING ENDPOINTS =============

import re
from urllib.parse import urlparse


def detect_ats_from_url(url: str) -> dict:
    """
    Detect ATS type and extract company info from job URL.
    Returns: {ats, company, board_url, job_id} or {error}
    """
    url = url.strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path
    
    # Greenhouse: boards.greenhouse.io/company/jobs/123 or company.com/jobs?gh_jid=123
    if "greenhouse.io" in host:
        # https://boards.greenhouse.io/stripe/jobs/7294977
        match = re.match(r"/([^/]+)/jobs/(\d+)", path)
        if match:
            company = match.group(1)
            job_id = match.group(2)
            return {
                "ats": "greenhouse",
                "company": company.replace("-", " ").title(),
                "company_slug": company,
                "board_url": f"https://boards.greenhouse.io/{company}",
                "job_id": job_id,
                "job_api_url": f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
            }
    
    if "gh_jid" in url:
        # https://stripe.com/jobs/search?gh_jid=7294977
        match = re.search(r"gh_jid=(\d+)", url)
        if match:
            job_id = match.group(1)
            # Extract company from domain
            company = host.replace("www.", "").split(".")[0]
            return {
                "ats": "greenhouse",
                "company": company.title(),
                "company_slug": company,
                "board_url": f"https://boards.greenhouse.io/{company}",
                "job_id": job_id,
                "job_api_url": f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
            }
    
    # Workday: company.wd1.myworkdayjobs.com/site/job/..._JOBID
    if "myworkdayjobs.com" in host:
        # https://hitachi.wd1.myworkdayjobs.com/en-US/hitachi/job/..._R0082977
        parts = host.split(".")
        company = parts[0] if parts else ""
        
        # Extract job ID from path (usually ends with _JOBID)
        job_id_match = re.search(r"_([A-Z0-9]+)$", path)
        job_id = job_id_match.group(1) if job_id_match else ""
        
        # Extract site name from path
        site_match = re.search(r"myworkdayjobs\.com/(?:en-US/)?([^/]+)", url)
        site = site_match.group(1) if site_match else company
        
        return {
            "ats": "workday",
            "company": company.title(),
            "company_slug": company,
            "board_url": f"https://{company}.wd1.myworkdayjobs.com/{site}",
            "job_id": job_id,
            "job_path": path
        }
    
    # Lever: jobs.lever.co/company/job-uuid
    if "lever.co" in host:
        match = re.match(r"/([^/]+)/([a-f0-9-]+)", path)
        if match:
            company = match.group(1)
            job_id = match.group(2)
            return {
                "ats": "lever",
                "company": company.replace("-", " ").title(),
                "company_slug": company,
                "board_url": f"https://jobs.lever.co/{company}",
                "job_id": job_id,
                "job_api_url": f"https://api.lever.co/v0/postings/{company}/{job_id}"
            }
    
    # SmartRecruiters: jobs.smartrecruiters.com/Company/job-id
    if "smartrecruiters.com" in host:
        match = re.match(r"/([^/]+)/([^/]+)", path)
        if match:
            company = match.group(1)
            job_id = match.group(2)
            return {
                "ats": "smartrecruiters",
                "company": company.replace("-", " ").title(),
                "company_slug": company,
                "board_url": f"https://jobs.smartrecruiters.com/{company}",
                "job_id": job_id
            }
    
    # Ashby: jobs.ashbyhq.com/company/job-uuid
    if "ashbyhq.com" in host:
        match = re.match(r"/([^/]+)/([a-f0-9-]+)", path)
        if match:
            company = match.group(1)
            job_id = match.group(2)
            return {
                "ats": "ashby",
                "company": company.replace("-", " ").title(),
                "company_slug": company,
                "board_url": f"https://jobs.ashbyhq.com/{company}",
                "job_id": job_id
            }
    
    # Unknown ATS - use universal parser
    # Extract company from domain
    company = host.replace("www.", "").replace("jobs.", "").split(".")[0]
    return {
        "ats": "universal",
        "company": company.title(),
        "company_slug": company,
        "board_url": f"https://{host}",
        "job_url": url
    }


def fetch_single_job(ats_info: dict) -> dict:
    """
    Fetch single job details from ATS.
    Returns job dict or {error}
    """
    import requests
    
    ats = ats_info.get("ats")
    
    if ats == "greenhouse":
        api_url = ats_info.get("job_api_url")
        try:
            resp = requests.get(api_url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "title": data.get("title", ""),
                    "location": data.get("location", {}).get("name", ""),
                    "url": data.get("absolute_url", ""),
                    "updated_at": data.get("updated_at", ""),
                    "ats_job_id": str(data.get("id", "")),
                }
            else:
                return {"error": f"Greenhouse API returned {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}
    
    elif ats == "workday":
        # Search by job ID
        company_slug = ats_info.get("company_slug")
        job_id = ats_info.get("job_id")
        board_url = ats_info.get("board_url")
        
        # Extract site from board_url
        site_match = re.search(r"myworkdayjobs\.com/([^/]+)", board_url)
        site = site_match.group(1) if site_match else company_slug
        
        search_url = f"https://{company_slug}.wd1.myworkdayjobs.com/wday/cxs/{company_slug}/{site}/jobs"
        try:
            resp = requests.post(
                search_url,
                json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": job_id},
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                postings = data.get("jobPostings", [])
                if postings:
                    job = postings[0]
                    return {
                        "title": job.get("title", ""),
                        "location": job.get("locationsText", ""),
                        "url": f"https://{company_slug}.wd1.myworkdayjobs.com{job.get('externalPath', '')}",
                        "ats_job_id": job_id,
                    }
                else:
                    return {"error": f"Job {job_id} not found"}
            else:
                return {"error": f"Workday API returned {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}
    
    elif ats == "lever":
        api_url = ats_info.get("job_api_url")
        try:
            resp = requests.get(api_url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "title": data.get("text", ""),
                    "location": data.get("categories", {}).get("location", ""),
                    "url": data.get("hostedUrl", ""),
                    "ats_job_id": data.get("id", ""),
                }
            else:
                return {"error": f"Lever API returned {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}
    
    elif ats == "universal":
        # Use Playwright-based universal parser
        try:
            from parsers.universal import extract_job_details
            result = extract_job_details(ats_info.get("job_url"))
            if result.get("error"):
                return {"error": result["error"]}
            return {
                "title": result.get("title", ""),
                "location": result.get("location", ""),
                "url": result.get("url", ats_info.get("job_url")),
                "description": result.get("description", ""),
                "salary": result.get("salary", ""),
            }
        except Exception as e:
            return {"error": f"Universal parser error: {str(e)}"}
    
    return {"error": f"Fetch not implemented for ATS: {ats}"}


class OnboardRequest(BaseModel):
    url: str


@app.post("/onboard")
def onboard_job(payload: OnboardRequest):
    """
    Onboard a new job by URL.
    1. Detect ATS and extract company info
    2. Add company if new
    3. Fetch single job details
    4. Classify role
    5. Add to pipeline if relevant
    """
    url = payload.url.strip()
    
    # 1. Detect ATS
    ats_info = detect_ats_from_url(url)
    if "error" in ats_info:
        return {"ok": False, "error": ats_info["error"]}
    
    ats = ats_info["ats"]
    company_name = ats_info["company"]
    company_slug = ats_info["company_slug"]
    board_url = ats_info["board_url"]
    
    # Fix company name using AI if it looks like a URL slug
    if ats == "universal" and (company_name.lower() == company_slug or "careers" in company_name.lower()):
        try:
            from utils.ollama_ai import fix_company_name, is_ollama_available
            if is_ollama_available():
                fixed_name = fix_company_name(company_name, board_url)
                if fixed_name and fixed_name != company_name:
                    company_name = fixed_name
        except Exception as e:
            print(f"AI company name fix error: {e}")
    
    # 2. Check if company exists
    companies_path = Path("data/companies.json")
    companies = json.load(open(companies_path)) if companies_path.exists() else []
    
    company_exists = any(
        c.get("id") == company_slug or c.get("name", "").lower() == company_name.lower()
        for c in companies
    )
    
    new_company = None
    if not company_exists:
        new_company = {
            "id": company_slug,
            "name": company_name,
            "ats": ats,
            "board_url": board_url,
            "industry": "",
            "tags": [],
            "priority": 0,
            "hq_state": None,
            "region": "global",
            "enabled": True
        }
        companies.append(new_company)
        with open(companies_path, "w") as f:
            json.dump(companies, f, indent=2, ensure_ascii=False)
    
    # 3. Fetch single job
    job_data = fetch_single_job(ats_info)
    if "error" in job_data:
        return {
            "ok": False, 
            "error": job_data["error"],
            "company": {"name": company_name, "new": new_company is not None}
        }
    
    # 4. Build full job object
    job = {
        "company": company_name,
        "ats": ats,
        "ats_job_id": job_data.get("ats_job_id", ats_info.get("job_id", "")),
        "title": job_data.get("title", ""),
        "location": job_data.get("location", ""),
        "job_url": job_data.get("url", url),
        "updated_at": job_data.get("updated_at", datetime.now(timezone.utc).isoformat()),
    }
    
    # Generate ID
    job["id"] = generate_job_id(job)
    
    # Normalize location
    loc_norm = normalize_location(job.get("location"))
    job["location_norm"] = loc_norm
    
    # Classify role (rule-based first, then AI fallback)
    role = classify_role(job.get("title"), job_data.get("description", ""))
    
    # If rule-based classification failed, try AI
    if role.get("role_family") == "other" and role.get("confidence", 0) < 60:
        try:
            from utils.ollama_ai import classify_role_ai, is_ollama_available
            if is_ollama_available():
                ai_role = classify_role_ai(job.get("title"), job_data.get("description", ""))
                if ai_role.get("confidence", 0) > role.get("confidence", 0):
                    role = ai_role
                    role["role_category"] = "ai_classified"
        except Exception as e:
            print(f"AI classification error: {e}")
    
    job["role_family"] = role.get("role_family")
    job["role_category"] = role.get("role_category")
    job["role_id"] = role.get("role_id")
    job["role_confidence"] = role.get("confidence")
    job["role_reason"] = role.get("reason")
    job["role_excluded"] = role.get("excluded", False)
    
    # Geo scoring
    bucket, score = compute_geo_bucket_and_score(loc_norm)
    job["geo_bucket"] = bucket
    job["geo_score"] = score
    
    # Company data
    job["company_data"] = {
        "priority": 0,
        "hq_state": None,
        "region": "global",
        "tags": []
    }
    
    job["source"] = "onboard"
    
    # 5. Add to pipeline if relevant role
    added_to_pipeline = False
    if job.get("role_family") in MY_ROLE_FAMILIES and not job.get("role_excluded"):
        # Check if already exists
        existing = get_job_by_id(job["id"])
        if not existing:
            add_job(job)
            added_to_pipeline = True
    
    return {
        "ok": True,
        "company": {
            "name": company_name,
            "new": new_company is not None,
            "ats": ats
        },
        "job": {
            "id": job["id"],
            "title": job["title"],
            "location": job["location"],
            "url": job["job_url"]
        },
        "classification": {
            "role_family": job.get("role_family"),
            "role_id": job.get("role_id"),
            "confidence": job.get("role_confidence"),
            "reason": job.get("role_reason")
        },
        "geo": {
            "bucket": bucket,
            "score": score
        },
        "added_to_pipeline": added_to_pipeline
    }


# ============= APPLY AUTOMATION ENDPOINTS =============

class ApplyRequest(BaseModel):
    job_url: str
    profile: str = "anton_tpm"


@app.post("/apply/greenhouse")
def apply_greenhouse_endpoint(payload: ApplyRequest):
    """
    Open Greenhouse job application and auto-fill form using SmartFillerV35.
    """
    import subprocess
    import sys
    import os
    
    job_url = payload.job_url
    profile_name = payload.profile
    
    if "greenhouse" not in job_url.lower() and "gh_jid" not in job_url.lower():
        return {"ok": False, "error": "Only Greenhouse URLs supported"}
    
    profile_path = Path(f"browser/profiles/{profile_name}.json")
    if not profile_path.exists():
        return {"ok": False, "error": f"Profile '{profile_name}' not found"}
    
    # Use symlink path without spaces for subprocess compatibility
    cwd = os.path.dirname(os.path.abspath(__file__))
    if "Mobile Documents" in cwd:
        cwd = cwd.replace(
            "/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects",
            "/Users/antonkondakov/icloud-projects"
        )
    
    # Write script to file to avoid shell escaping issues
    script_file = "/tmp/greenhouse_apply_script.py"
    with open(script_file, "w") as f:
        f.write(f'''
import sys
sys.path.insert(0, '{cwd}')
import os
os.chdir('{cwd}')

from browser.smart_filler_v35 import SmartFillerV35
import re

job_url = "{job_url}"

# Convert company career page URL to direct Greenhouse form URL
if 'gh_jid=' in job_url and 'job-boards.greenhouse.io' not in job_url:
    match = re.search(r'gh_jid=(\\d+)', job_url)
    if match:
        gh_jid = match.group(1)
        company_match = re.search(r'https?://(?:www\\.)?([^/]+)\\.com', job_url)
        company = company_match.group(1) if company_match else 'company'
        job_url = "https://job-boards.greenhouse.io/embed/job_app?token=" + gh_jid + "&for=" + company + "&gh_jid=" + gh_jid
        print("Converted to direct Greenhouse URL: " + job_url)

try:
    filler = SmartFillerV35(headless=False)
    filler.run(job_url, interactive=False)
    
    print("\\n" + "="*60)
    print("Browser will stay open for 60 seconds for review...")
    print("="*60)
    
    import time
    time.sleep(60)
except KeyboardInterrupt:
    pass
except Exception as e:
    print(f"Error: {{e}}")
    import traceback
    traceback.print_exc()
    import time
    time.sleep(10)
finally:
    if 'filler' in dir() and filler:
        filler.stop()
    print("Browser closed")
''')
    
    # Run script in background
    log_file = "/tmp/apply_greenhouse.log"
    with open(log_file, "w") as log:
        log.write(f"Starting apply for: {job_url}\n")
        log.write(f"Profile: {profile_name}\n")
        log.write("="*60 + "\n")
    
    # Start subprocess
    process = subprocess.Popen(
        [sys.executable, script_file],
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        cwd=cwd
    )
    
    return {
        "ok": True,
        "message": "Application form opened with SmartFiller V3.5",
        "pid": process.pid,
        "log_file": log_file
    }

@app.get("/answers")
def get_answer_library():
    """Get the full answer library."""
    path = Path("data/answer_library.json")
    if not path.exists():
        return {"personal": {}, "links": {}, "answers": {}, "cover_letter_template": {}}
    with open(path) as f:
        return json.load(f)


@app.put("/answers")
def update_answer_library(data: dict):
    """Update the answer library."""
    path = Path("data/answer_library.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"ok": True}


@app.get("/answers/{category}/{key}")
def get_answer(category: str, key: str):
    """Get a specific answer."""
    path = Path("data/answer_library.json")
    if not path.exists():
        return {"error": "Answer library not found"}
    with open(path) as f:
        data = json.load(f)
    
    if category in data and key in data[category]:
        return {"value": data[category][key]}
    return {"error": f"Key {category}/{key} not found"}


@app.post("/generate-cover-letter")
def generate_cover_letter(payload: dict):
    """
    Generate a personalized cover letter.
    Expects: {company, position, job_description?, highlights?}
    """
    company = payload.get("company", "[Company]")
    position = payload.get("position", "[Position]")
    job_description = payload.get("job_description", "")
    
    # Load answer library
    path = Path("data/answer_library.json")
    if not path.exists():
        return {"error": "Answer library not found"}
    with open(path) as f:
        library = json.load(f)
    
    template = library.get("cover_letter_template", {})
    personal = library.get("personal", {})
    
    # Try AI-generated bullet points
    bullets = []
    try:
        from utils.ollama_ai import generate_cover_letter_points, is_ollama_available
        if is_ollama_available() and job_description:
            cv_highlights = f"""
            - 15+ years TPM/Program Manager experience
            - Led teams of 50+ engineers across global time zones
            - GCP cloud migration: $1.2M savings, 25% uptime improvement
            - Deutsche Bank, UBS, DXC Technology experience
            - SAFe POPM certified, Scrum Master
            """
            bullets = generate_cover_letter_points(position, company, job_description, cv_highlights)
    except Exception as e:
        print(f"AI cover letter error: {e}")
    
    # Fallback bullets
    if not bullets:
        bullets = [
            "15+ years leading complex technology programs for Fortune 500 companies",
            "Proven track record delivering cloud migrations with $1.2M+ cost savings",
            "Experience managing cross-functional teams of 50+ engineers across global time zones"
        ]
    
    # Build cover letter
    opening = template.get("opening", "").replace("[POSITION]", position).replace("[COMPANY]", company)
    closing = template.get("closing", "").replace("[COMPANY]", company)
    
    bullet_text = "\n".join([f"• {b}" for b in bullets])
    body = f"My experience directly addresses your key requirements:\n\n{bullet_text}"
    
    cover_letter = f"""{personal.get('full_name', 'Anton Kondakov')}
{personal.get('phone', '')} | {personal.get('email', '')} | {personal.get('linkedin', '')}
{personal.get('location', '')}

Dear Hiring Manager,

{opening}

{body}

{closing}

Sincerely,
{personal.get('full_name', 'Anton Kondakov')}
"""
    
    return {
        "ok": True,
        "cover_letter": cover_letter,
        "bullets": bullets,
        "company": company,
        "position": position
    }


@app.post("/save-cover-letter")
def save_cover_letter(payload: dict):
    """
    Save cover letter to file.
    Expects: {company, position, content}
    Returns: {ok, file_path}
    """
    company = payload.get("company", "Unknown").replace(" ", "_").replace("/", "_")
    position = payload.get("position", "Position").replace(" ", "_").replace("/", "_")
    content = payload.get("content", "")
    
    if not content:
        return {"error": "No content provided"}
    
    # Create cover letters folder
    cover_letters_dir = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/cover_letters")
    cover_letters_dir.mkdir(exist_ok=True)
    
    # Save as text file (can convert to PDF later)
    filename = f"{company}_{position}_Cover_Letter.txt"
    file_path = cover_letters_dir / filename
    
    with open(file_path, "w") as f:
        f.write(content)
    
    return {
        "ok": True,
        "file_path": str(file_path),
        "filename": filename
    }


@app.get("/available-cvs")
def get_available_cvs():
    """
    Get list of available CV files.
    """
    cv_dir = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV")
    cvs = []
    
    for f in cv_dir.glob("*.pdf"):
        cvs.append({"path": str(f), "name": f.name})
    
    # Also check for docx
    for f in cv_dir.glob("*CV*.docx"):
        if not f.name.startswith("~"):
            cvs.append({"path": str(f), "name": f.name})
    
    return {"cvs": cvs}


@app.post("/select-cv")
def select_cv_for_job(payload: dict):
    """
    Use AI to select best CV for a job.
    Expects: {job_title}
    Returns: {selected_cv, reason}
    """
    job_title = payload.get("job_title", "")
    
    # Get available CVs
    cv_dir = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV")
    available_cvs = [str(f) for f in cv_dir.glob("*.pdf")]
    
    if not available_cvs:
        return {"error": "No CV files found"}
    
    try:
        from utils.ollama_ai import select_cv_for_role, is_ollama_available
        
        if is_ollama_available():
            selected = select_cv_for_role(job_title, available_cvs)
            return {
                "ok": True,
                "selected_cv": selected,
                "filename": Path(selected).name if selected else None,
                "ai_selected": True
            }
    except Exception as e:
        print(f"AI CV selection error: {e}")
    
    # Fallback: simple keyword matching
    title_lower = job_title.lower()
    for cv in available_cvs:
        cv_lower = cv.lower()
        if 'tpm' in title_lower and 'tpm' in cv_lower:
            return {"ok": True, "selected_cv": cv, "filename": Path(cv).name, "ai_selected": False}
        if 'product' in title_lower and 'product' in cv_lower:
            return {"ok": True, "selected_cv": cv, "filename": Path(cv).name, "ai_selected": False}
        if 'program' in title_lower and 'tpm' in cv_lower:
            return {"ok": True, "selected_cv": cv, "filename": Path(cv).name, "ai_selected": False}
    
    return {"ok": True, "selected_cv": available_cvs[0], "filename": Path(available_cvs[0]).name, "ai_selected": False}


@app.get("/apply-log")
def get_apply_log():
    """
    Get the latest apply log content.
    """
    log_path = Path("/tmp/greenhouse_apply.log")
    if not log_path.exists():
        return {"ok": False, "log": "No log file found"}
    
    try:
        with open(log_path, "r") as f:
            content = f.read()
        return {"ok": True, "log": content}
    except Exception as e:
        return {"ok": False, "log": f"Error reading log: {e}"}


@app.post("/fetch-job-description")
def fetch_job_description(payload: dict):
    """
    Fetch job description from URL.
    Expects: {url}
    Returns: {ok, description}
    """
    import requests
    from bs4 import BeautifulSoup
    
    url = payload.get("url", "")
    if not url:
        return {"ok": False, "error": "No URL provided"}
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Try different selectors for job description
        description = ""
        
        # Greenhouse
        content = soup.select_one("#content, .content, [data-automation='job-description']")
        if content:
            description = content.get_text(separator="\n", strip=True)
        
        # Lever
        if not description:
            content = soup.select_one(".posting-page, .section-wrapper")
            if content:
                description = content.get_text(separator="\n", strip=True)
        
        # Generic fallback - main content area
        if not description:
            for selector in ["main", "article", ".job-description", ".description", "#job-content"]:
                content = soup.select_one(selector)
                if content:
                    description = content.get_text(separator="\n", strip=True)
                    break
        
        # Clean up - remove excessive whitespace, limit length
        if description:
            lines = [line.strip() for line in description.split("\n") if line.strip()]
            description = "\n".join(lines[:100])  # Limit to ~100 lines
            description = description[:3000]  # Limit to 3000 chars
        
        if description:
            return {"ok": True, "description": description}
        else:
            return {"ok": False, "error": "Could not extract job description"}
            
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/apply/vision")
def apply_with_vision(payload: ApplyRequest):
    """
    Apply to job using Vision AI Agent.
    AI looks at screenshots and fills form like a human.
    """
    import subprocess
    import sys
    import os
    
    job_url = payload.job_url
    profile_name = payload.profile
    
    profile_path = Path(f"browser/profiles/{profile_name}.json")
    if not profile_path.exists():
        return {"ok": False, "error": f"Profile '{profile_name}' not found"}
    
    # Load profile data
    with open(profile_path) as f:
        profile_data = json.load(f)
    
    cwd = os.path.dirname(os.path.abspath(__file__))
    if "Mobile Documents" in cwd:
        cwd = cwd.replace(
            "/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects",
            "/Users/antonkondakov/icloud-projects"
        )
    
    script = f'''import sys
sys.path.insert(0, '{cwd}')
import os
os.chdir('{cwd}')

from browser.client import BrowserClient
from browser.vision_agent import VisionFormAgent
import json
import time

# Load profile from file
with open("browser/profiles/{profile_name}.json") as f:
    profile = json.load(f)

# Start browser
browser = BrowserClient()
browser.start()

try:
    # Open job page
    browser.open_job_page("{job_url}")
    time.sleep(3)
    
    # Start Vision Agent
    agent = VisionFormAgent(browser.page, profile)
    result = agent.fill_form()
    
    print("\\n" + "="*50)
    print(f"Result: {{result}}")
    print("="*50)
    
    # Keep open for review
    print("\\nBrowser stays open for 60 seconds...")
    time.sleep(60)
    
except Exception as e:
    print(f"Error: {{e}}")
    import traceback
    traceback.print_exc()
    time.sleep(10)
finally:
    browser.close()
'''
    
    # Write and execute script
    script_file = "/tmp/vision_apply_script.py"
    log_file = "/tmp/vision_apply.log"
    
    with open(script_file, "w") as f:
        f.write(script)
    
    # Run in background
    subprocess.Popen(
        [sys.executable, script_file],
        stdout=open(log_file, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )
    
    return {
        "ok": True,
        "message": f"Vision AI Agent started for {job_url}",
        "log_file": log_file,
        "screenshots_dir": "/tmp/vision_agent"
    }
