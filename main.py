# I have access to - main.py

from __future__ import annotations

import os
from datetime import datetime, timezone
import json
from collections import Counter
from pathlib import Path
from typing import Any

# Environment: PROD or DEV
ENV = os.getenv("JOB_TRACKER_ENV", "PROD")

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from parsers.greenhouse import fetch_greenhouse
from parsers.lever import fetch_lever
from parsers.smartrecruiters import fetch_smartrecruiters
from parsers.ashby import fetch_ashby_jobs
from ats_detector import try_repair_company, verify_ats_url
from company_storage import load_profile
from utils.normalize import normalize_location, STATE_MAP
from utils.cache_manager import load_cache, save_cache, clear_cache, get_cache_info, load_stats
from utils.job_utils import generate_job_id, classify_role, find_similar_jobs
from storage.pipeline_storage import (
    load_new_jobs, load_pipeline_jobs, load_archive_jobs,
    get_all_job_ids, add_new_job, update_job_status as pipeline_update_status,
    update_last_seen, mark_missing_jobs, get_pipeline_stats, get_job_by_id,
    STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, STATUS_OFFER,
    STATUS_REJECTED, STATUS_WITHDRAWN, STATUS_CLOSED,
)


# -----------------------------
# Runtime/local state files
# -----------------------------
JOB_STATUS_FILE = Path("job_status.json")

VALID_APPLICATION_STATUSES = ["New", "Applied", "Interview", "Offer", "Rejected", "Withdrawn", "Closed"]


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
            jobs = fetch_smartrecruiters(company, url)
        elif ats == "ashby":
            jobs = fetch_ashby_jobs(url)
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
        
        for cfg in companies_cfg:
            # Skip disabled companies
            if cfg.get("enabled") == False:
                continue
            ats = cfg.get("ats", "")
            if ats_filter != "all" and ats_filter != ats:
                continue
            jobs = _fetch_for_company(profile, cfg)
            all_jobs.extend(jobs)
        
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
                add_new_job(job)
        
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
    all_pipeline_jobs = load_new_jobs() + load_pipeline_jobs()
    
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
        # Skip disabled companies
        if cfg.get("enabled") == False:
            continue
            
        company_name = cfg.get("company", "") or cfg.get("name", "")
        key = f"{profile}:{company_name}"
        st = company_fetch_status.get(key, {})
        
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
                "status": cfg.get("status", "ok"),
                "last_ok": st.get("ok", None),
                "last_error": st.get("error", ""),
                "last_checked": st.get("checked_at", ""),
                # Stats from pipeline
                "jobs_count": stats["jobs_count"],
                "new_count": stats["new_count"],
                "applied_count": stats["applied_count"],
                "interview_count": stats["interview_count"],
            }
        )

    # Sort by jobs_count desc, then by name
    items.sort(key=lambda x: (-x["jobs_count"], x["company"].lower()))
    
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


# ============= PIPELINE ENDPOINTS =============

@app.get("/pipeline/stats")
def pipeline_stats_endpoint():
    """Get pipeline statistics"""
    return get_pipeline_stats()


@app.get("/jobs/review")
def get_review_jobs(
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
def pipeline_all_endpoint():
    """Get ALL jobs from pipeline (new + active + archive)"""
    new_jobs = load_new_jobs()
    active_jobs = load_pipeline_jobs()
    archive_jobs = load_archive_jobs()
    all_jobs = new_jobs + active_jobs + archive_jobs
    return {
        "count": len(all_jobs), 
        "jobs": all_jobs,
        "breakdown": {
            "new": len(new_jobs),
            "active": len(active_jobs),
            "archive": len(archive_jobs)
        }
    }


@app.get("/pipeline/new")
def pipeline_new_endpoint():
    """Get new (inbox) jobs"""
    jobs = load_new_jobs()
    return {"count": len(jobs), "jobs": jobs}


@app.get("/pipeline/active")
def pipeline_active_endpoint():
    """Get active pipeline jobs (Applied, Interview, Closed)"""
    jobs = load_pipeline_jobs()
    return {"count": len(jobs), "jobs": jobs}


@app.get("/pipeline/archive")
def pipeline_archive_endpoint():
    """Get archived jobs (Rejected, Offer, Withdrawn)"""
    jobs = load_archive_jobs()
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
    added_job = add_new_job(job)
    
    if added_job:
        return {"ok": True, "job": added_job}
    else:
        return {"ok": False, "error": "Failed to add job"}


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
    Valid statuses: New, Applied, Interview, Offer, Rejected, Withdrawn, Closed
    """
    valid_statuses = [STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW, 
                      STATUS_OFFER, STATUS_REJECTED, STATUS_WITHDRAWN, STATUS_CLOSED]
    
    if payload.status not in valid_statuses:
        return {"ok": False, "error": f"Invalid status. Valid: {valid_statuses}"}
    
    job = pipeline_update_status(payload.job_id, payload.status, payload.notes)
    
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
    pipeline = load_pipeline_jobs()
    attention = [j for j in pipeline if j.get("needs_attention")]
    return {"count": len(attention), "jobs": attention}
