from __future__ import annotations

from datetime import datetime, timezone
from collections import Counter
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from parsers.greenhouse import fetch_greenhouse
from parsers.lever import fetch_lever

# Если у тебя нет файла parsers/smartrecruiters.py — закомментируй следующий импорт
from parsers.smartrecruiters import fetch_smartrecruiters

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


def _is_us_location(location: str | None) -> bool:
    if not location:
        return False
    loc = location.lower()
    if "united states" in loc or "usa" in loc or "us" in loc:
        return True
    us_markers = [
        ", ca",
        ", ny",
        ", wa",
        ", ma",
        ", tx",
        ", co",
        ", il",
        ", ga",
        ", nc",
        "washington, dc",
        "new york, ny",
        "san francisco",
        "remote - us",
    ]
    return any(m in loc for m in us_markers)


def _parse_date_to_utc(datestr: str | None) -> datetime | None:
    """
    Parse ISO-ish string into timezone-aware UTC datetime.
    Accepts 'Z' suffix.
    If parsed datetime is naive, assume UTC.
    """
    if not datestr:
        return None
    try:
        ds = datestr.strip()
        if ds.endswith("Z"):
            ds = ds[:-1] + "+00:00"
        dt = datetime.fromisoformat(ds)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_cfg(cfg: dict) -> dict:
    """
    Support both schemas:
      - {company, url}
      - {name, board_url}
    Keep original cfg fields too.
    """
    out = dict(cfg or {})
    out["company"] = cfg.get("company") or cfg.get("name") or ""
    out["url"] = cfg.get("url") or cfg.get("board_url") or ""
    out["ats"] = cfg.get("ats", "") or ""
    out["industry"] = cfg.get("industry", "") or ""
    out["api_url"] = cfg.get("api_url") or cfg.get("apiEndpoint") or ""
    return out


def _ats_label(cfg: dict) -> str:
    """
    If ATS has no API access (or we rely on HTML scraping), show 'no Access'.
    For now: mark Workday types as no Access unless api_url is provided.
    """
    ats = (cfg.get("ats") or "").strip()
    api_url = (cfg.get("api_url") or "").strip()

    if ats in {"workday", "workday_json"} and not api_url:
        return "no Access"
    return ats or ""


def _mark_company_status(profile: str, cfg: dict, ok: bool, error: str | None = None):
    cfgn = _normalize_cfg(cfg)
    key = f"{profile}:{cfgn.get('company', '')}"
    company_fetch_status[key] = {
        "ok": ok,
        "error": error or "",
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "ats": cfgn.get("ats", ""),
        "url": cfgn.get("url", ""),
    }


def _fetch_for_company(profile: str, cfg: dict) -> list[dict]:
    """
    Unified ATS fetch + enrich job fields:
      - location_norm
      - role_family, role_confidence, role_reason
      - company_data
      - geo_bucket, geo_score
      - score (computed later in /jobs)
    """
    cfgn = _normalize_cfg(cfg)
    company = cfgn.get("company", "")
    ats = cfgn.get("ats", "")
    url = cfgn.get("url", "")

    try:
        if ats == "greenhouse":
            jobs = fetch_greenhouse(company, url)
        elif ats == "lever":
            jobs = fetch_lever(company, url)
        elif ats == "smartrecruiters":
            jobs = fetch_smartrecruiters(company, url)
        else:
            jobs = []

        _mark_company_status(profile, cfgn, ok=True)

        for j in jobs:
            j["company"] = company
            j["industry"] = cfgn.get("industry", "")
            j["ats"] = ats

            # normalize location
            loc_norm = normalize_location(j.get("location"))
            j["location_norm"] = loc_norm

            # classify role
            role = classify_role(j.get("title"), j.get("description") or j.get("jd") or "")
            j["role_family"] = role.get("role_family")
            j["role_confidence"] = role.get("confidence")
            j["role_reason"] = role.get("reason")

            # company config data
            j["company_data"] = {
                "priority": cfgn.get("priority", 0),
                "hq_state": cfgn.get("hq_state", None),
                "region": cfgn.get("region", None),
                "tags": cfgn.get("tags", []),
            }

            # geo bucket + score
            bucket, score = compute_geo_bucket_and_score(loc_norm)
            j["geo_bucket"] = bucket
            j["geo_score"] = score

        return jobs

    except Exception as e:  # noqa: BLE001
        _mark_company_status(profile, cfgn, ok=False, error=str(e))
        print(f"Error for {company}: {e}")
        return []


def _match_geo_bucket(bucket: str, geo_mode: str) -> bool:
    """
    Server-side geo filter.
    geo_mode:
      - all
      - nc_priority (local, nc, neighbor, remote_usa)
      - local_only
      - neighbor_only
      - remote_usa
    """
    if geo_mode == "all":
        return True
    if geo_mode == "nc_priority":
        return bucket in {"local", "nc", "neighbor", "remote_usa"}
    if geo_mode == "local_only":
        return bucket == "local"
    if geo_mode == "neighbor_only":
        return bucket == "neighbor"
    if geo_mode == "remote_usa":
        return bucket == "remote_usa"
    return True


app = FastAPI(
    title="Job Tracker",
    description="Simple job aggregator for Product / PM roles from ATS",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/jobs")
async def get_jobs(
    profile: str = Query("all", description="profiles/*.json (или all)"),
    ats_filter: str = Query("all", description="all / greenhouse / lever / smartrecruiters"),
    role_filter: str = Query("all", description="all / product / tpm_program / project / other"),
    location_filter: str = Query("all", description="all / us / nonus"),
    company_filter: str = Query("", description="substring match"),
    search: str = Query("", description="search title/location/company"),
    states: str = Query("", description="Comma-separated states, e.g. NC,VA,South Carolina"),
    include_remote_usa: bool = Query(False, description="If true and states provided: state OR Remote-USA"),
    state: str = Query("", description="(deprecated) state substring filter"),
    city: str = Query("", description="city substring filter"),
    geo_mode: str = Query("nc_priority", description="all / nc_priority / local_only / neighbor_only / remote_usa"),
):
    companies_cfg = load_profile(profile)
    all_jobs: list[dict] = []

    for cfg in companies_cfg:
        cfgn = _normalize_cfg(cfg)
        ats = cfgn.get("ats", "")
        if ats_filter != "all" and ats_filter != ats:
            continue
        all_jobs.extend(_fetch_for_company(profile, cfgn))

    # --- filters ---
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

        job_states: list[str] = []
        if isinstance(loc_norm.get("states"), list):
            job_states.extend([str(st).upper() for st in (loc_norm.get("states") or []) if st])
        if loc_norm.get("state"):
            job_states.append(str(loc_norm.get("state")).upper())
        if loc_norm.get("state_full"):
            sf = str(loc_norm.get("state_full")).lower()
            if sf in STATE_MAP:
                job_states.append(STATE_MAP[sf])

        remote_usa = bool(loc_norm.get("remote")) and (str(loc_norm.get("remote_scope") or "").lower() == "usa")

        if not normalized_states and not include_remote_usa:
            return True

        state_matches = any(ns in job_states for ns in states_set_upper)

        if include_remote_usa and normalized_states:
            return state_matches or remote_usa
        if include_remote_usa and not normalized_states:
            return remote_usa
        return state_matches

    def match_old_state(job: dict) -> bool:
        if not state:
            return True
        loc = job.get("location", "") or ""
        return state.lower() in loc.lower()

    def match_city(job: dict) -> bool:
        if not city:
            return True
        loc_norm = job.get("location_norm", {}) or {}
        if loc_norm:
            return city.lower() in str(loc_norm.get("city") or "").lower()
        loc = job.get("location", "") or ""
        return city.lower() in loc.lower()

    def match_geo(job: dict) -> bool:
        bucket = job.get("geo_bucket", "unknown")
        return _match_geo_bucket(bucket, geo_mode)

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

    # --- score + sort ---
    now = datetime.now(timezone.utc)
    for job in filtered:
        score = 0

        loc_norm = job.get("location_norm", {}) or {}
        job_state_upper = (loc_norm.get("state") or "").upper()
        job_city_lower = (loc_norm.get("city") or "").lower()
        job_remote_scope = (loc_norm.get("remote_scope") or "").lower()
        job_remote = bool(loc_norm.get("remote"))

        # Explicit state selection boost
        if states_set_upper and job_state_upper in states_set_upper:
            score += 30

        # Remote-USA boost when option enabled
        if include_remote_usa and job_remote_scope == "usa":
            score += 20

        # small credit for any remote when no explicit geo filter provided
        if not states_set_upper and not city and job_remote:
            score += 5

        # Company priority
        company_data = job.get("company_data") or {}
        score += int(company_data.get("priority") or 0)

        # Freshness penalty
        updated = _parse_date_to_utc(job.get("updated_at"))
        if updated:
            age_days = (now - updated).days
            if age_days > 60:
                score -= 20
            elif age_days > 30:
                score -= 10

        # Main weight: geo_score
        score += int(job.get("geo_score", 0))

        job["score"] = score

    filtered.sort(key=lambda j: (j.get("score", 0), str(j.get("updated_at") or "")), reverse=True)

    return {"count": len(filtered), "jobs": filtered}


@app.get("/companies")
def get_companies(
    profile: str = Query("all", description="profiles/*.json (или all)"),
    include_counts: bool = Query(True, description="If true: fetch jobs per company to compute counts"),
    geo_mode: str = Query("nc_priority", description="Same as /jobs geo_mode"),
):
    """
    Companies list:
      - company
      - total jobs (positions_total)
      - priority jobs (positions_priority) according to geo_mode
      - ats label (no Access)
      - board url (jobs_list_url)
      - last status
    Sorted: priority desc, total desc, company asc
    """
    companies_cfg = load_profile(profile)
    items: list[dict[str, Any]] = []

    for cfg in companies_cfg:
        cfgn = _normalize_cfg(cfg)
        company_name = cfgn.get("company", "")
        key = f"{profile}:{company_name}"
        st = company_fetch_status.get(key, {})

        positions_total = None
        positions_priority = None

        if include_counts:
            jobs = _fetch_for_company(profile, cfgn)
            positions_total = len(jobs)
            positions_priority = sum(
                1 for j in jobs if _match_geo_bucket(j.get("geo_bucket", "unknown"), geo_mode)
            )

        items.append(
            {
                "company": company_name,
                "industry": cfgn.get("industry", ""),
                "ats": _ats_label(cfgn),
                "ats_raw": cfgn.get("ats", ""),
                "jobs_list_url": cfgn.get("url", ""),
                "api_url": cfgn.get("api_url", ""),
                "positions_total": positions_total,
                "positions_priority": positions_priority,
                "last_ok": st.get("ok", None),
                "last_error": st.get("error", ""),
                "last_checked": st.get("checked_at", ""),
            }
        )

    def _n(v):
        return int(v) if isinstance(v, int) else 0

    items.sort(
        key=lambda x: (
            _n(x.get("positions_priority")),
            _n(x.get("positions_total")),
            (x.get("company") or "").lower(),
        ),
        reverse=True,
    )

    return {"count": len(items), "companies": items}


@app.get("/profiles/{name}")
async def get_profile_companies(name: str):
    companies = load_profile(name)
    result_companies = []
    for c in companies:
        result_companies.append(
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "company": c.get("company"),
                "ats": c.get("ats", ""),
                "board_url": c.get("board_url", ""),
                "url": c.get("url", ""),
                "tags": c.get("tags", []),
                "priority": c.get("priority", 0),
                "hq_state": c.get("hq_state", None),
                "region": c.get("region", None),
            }
        )
    return {"count": len(result_companies), "companies": result_companies}


@app.get("/debug/location_stats")
async def location_stats(profile: str = Query("all")):
    companies_cfg = load_profile(profile)

    all_jobs: list[dict] = []
    for cfg in companies_cfg:
        all_jobs.extend(_fetch_for_company(profile, cfg))

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
