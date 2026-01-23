# storage/job_storage.py
"""
Unified Job Storage System

Single file: data/jobs.json
All jobs with status field - no moving between files.

Statuses:
- new        : Inbox, needs review
- applied    : Application submitted
- interview  : In interview process
- offer      : Received offer
- rejected   : Rejected by company
- withdrawn  : Withdrawn by user
- closed     : Disappeared from ATS
- excluded   : Hidden/not interested
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Set

DATA_DIR = Path(__file__).parent.parent / "data"
JOBS_FILE = DATA_DIR / "jobs_new.json"  # Unified with pipeline

# Статусы
STATUS_NEW = "new"
STATUS_APPLIED = "applied"
STATUS_INTERVIEW = "interview"
STATUS_OFFER = "offer"
STATUS_REJECTED = "rejected"
STATUS_WITHDRAWN = "withdrawn"
STATUS_CLOSED = "closed"
STATUS_EXCLUDED = "excluded"

# Группы для UI фильтрации
ACTIVE_STATUSES = {STATUS_NEW, STATUS_APPLIED, STATUS_INTERVIEW}
ARCHIVE_STATUSES = {STATUS_OFFER, STATUS_REJECTED, STATUS_WITHDRAWN, STATUS_CLOSED}
HIDDEN_STATUSES = {STATUS_EXCLUDED}

# Статусы требующие внимания (job исчез с ATS пока в активном статусе)
ATTENTION_STATUSES = {STATUS_APPLIED, STATUS_INTERVIEW}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_jobs() -> List[dict]:
    """Load all jobs from storage"""
    if not JOBS_FILE.exists():
        return []
    try:
        with JOBS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_jobs(jobs: List[dict]):
    """Save all jobs to storage"""
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with JOBS_FILE.open("w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


# ============ Query Functions ============

def get_all_jobs() -> List[dict]:
    """Get all jobs"""
    return _load_jobs()


def get_jobs_by_status(status: str) -> List[dict]:
    """Get jobs with specific status"""
    return [j for j in _load_jobs() if j.get("status") == status]


def get_jobs_by_statuses(statuses: Set[str]) -> List[dict]:
    """Get jobs with any of specified statuses"""
    return [j for j in _load_jobs() if j.get("status") in statuses]


def get_active_jobs() -> List[dict]:
    """Get jobs in active pipeline (new, applied, interview)"""
    return get_jobs_by_statuses(ACTIVE_STATUSES)


def get_archive_jobs() -> List[dict]:
    """Get archived jobs (offer, rejected, withdrawn, closed)"""
    return get_jobs_by_statuses(ARCHIVE_STATUSES)


def get_job_by_id(job_id: str) -> Optional[dict]:
    """Find job by ID"""
    for job in _load_jobs():
        if job.get("id") == job_id:
            return job
    return None


def get_all_job_ids() -> Set[str]:
    """Get set of all job IDs"""
    return {j.get("id") for j in _load_jobs() if j.get("id")}


def job_exists(job_id: str) -> bool:
    """Check if job already exists"""
    return job_id in get_all_job_ids()


# ============ Write Functions ============

def add_job(job: dict, status: str = STATUS_NEW) -> bool:
    """
    Add a new job to storage.
    Returns True if added, False if already exists.
    """
    job_id = job.get("id")
    if not job_id:
        return False
    
    jobs = _load_jobs()
    
    # Check if already exists
    if any(j.get("id") == job_id for j in jobs):
        return False
    
    now = _now_iso()
    job_record = {
        **job,
        "status": status,
        "status_history": [{"status": status, "date": now}],
        "first_seen": now,
        "last_seen": now,
        "is_active_on_ats": True,
        "needs_attention": False,
        "notes": "",
    }
    
    jobs.append(job_record)
    _save_jobs(jobs)
    return True


def add_jobs_bulk(new_jobs: List[dict], status: str = STATUS_NEW) -> int:
    """
    Add multiple jobs at once. More efficient than calling add_job repeatedly.
    Returns count of actually added jobs.
    """
    if not new_jobs:
        return 0
    
    jobs = _load_jobs()
    existing_ids = {j.get("id") for j in jobs}
    
    now = _now_iso()
    added = 0
    
    for job in new_jobs:
        job_id = job.get("id")
        if not job_id or job_id in existing_ids:
            continue
        
        job_record = {
            **job,
            "status": status,
            "status_history": [{"status": status, "date": now}],
            "first_seen": now,
            "last_seen": now,
            "is_active_on_ats": True,
            "needs_attention": False,
            "notes": "",
        }
        jobs.append(job_record)
        existing_ids.add(job_id)
        added += 1
    
    if added > 0:
        _save_jobs(jobs)
    
    return added


def update_status(job_id: str, new_status: str, notes: str = "", folder_path: str = "") -> Optional[dict]:
    """
    Update job status.
    Returns updated job or None if not found.
    """
    jobs = _load_jobs()
    now = _now_iso()
    
    for job in jobs:
        if job.get("id") == job_id:
            old_status = job.get("status")
            job["status"] = new_status
            job["status_history"] = job.get("status_history", [])
            job["status_history"].append({"status": new_status, "date": now})
            job["updated_at"] = now
            
            if folder_path:
                job["folder_path"] = folder_path
            if notes:
                job["notes"] = notes
            
            # Clear attention flag unless closing
            if new_status != STATUS_CLOSED:
                job["needs_attention"] = False
            
            _save_jobs(jobs)
            return job
    
    return None


def update_last_seen(job_id: str, is_active: bool = True) -> bool:
    """
    Update last_seen timestamp for a job.
    Called during parsing to mark job as still active on ATS.
    """
    jobs = _load_jobs()
    now = _now_iso()
    
    for job in jobs:
        if job.get("id") == job_id:
            job["last_seen"] = now
            job["is_active_on_ats"] = is_active
            _save_jobs(jobs)
            return True
    
    return False


def update_last_seen_bulk(job_ids: Set[str]) -> int:
    """
    Update last_seen for multiple jobs at once.
    Returns count of updated jobs.
    """
    if not job_ids:
        return 0
    
    jobs = _load_jobs()
    now = _now_iso()
    updated = 0
    
    for job in jobs:
        if job.get("id") in job_ids:
            job["last_seen"] = now
            job["is_active_on_ats"] = True
            updated += 1
    
    if updated > 0:
        _save_jobs(jobs)
    
    return updated


def mark_missing_jobs(active_job_ids: Set[str], days_threshold: int = 3) -> List[dict]:
    """
    Mark jobs as closed if they haven't been seen for days_threshold days.
    Only affects jobs in ATTENTION_STATUSES (applied, interview).
    Returns list of jobs that were marked as needing attention.
    """
    now = datetime.now(timezone.utc)
    jobs = _load_jobs()
    needs_attention = []
    changed = False
    
    for job in jobs:
        job_id = job.get("id")
        status = job.get("status")
        
        # Skip if not in attention statuses
        if status not in ATTENTION_STATUSES:
            continue
        
        if job_id in active_job_ids:
            # Still active on ATS
            if not job.get("is_active_on_ats", True):
                job["is_active_on_ats"] = True
                job["needs_attention"] = False
                changed = True
            continue
        
        # Not in active list - check how long missing
        job["is_active_on_ats"] = False
        
        last_seen = job.get("last_seen", "")
        if not last_seen:
            continue
        
        try:
            last_seen_dt = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            days_missing = (now - last_seen_dt).days
            
            if days_missing >= days_threshold:
                job["status"] = STATUS_CLOSED
                job["status_history"] = job.get("status_history", [])
                job["status_history"].append({
                    "status": STATUS_CLOSED,
                    "date": _now_iso(),
                    "reason": f"Not seen on ATS for {days_missing} days"
                })
                job["needs_attention"] = True
                needs_attention.append(job)
                changed = True
        except (ValueError, TypeError):
            pass
    
    if changed:
        _save_jobs(jobs)
    
    return needs_attention


def remove_job(job_id: str) -> bool:
    """
    Remove job from storage entirely.
    Use with caution - prefer update_status to excluded.
    """
    jobs = _load_jobs()
    original_len = len(jobs)
    jobs = [j for j in jobs if j.get("id") != job_id]
    
    if len(jobs) < original_len:
        _save_jobs(jobs)
        return True
    return False


# ============ Statistics ============

def get_stats() -> dict:
    """Get summary statistics"""
    jobs = _load_jobs()
    
    status_counts = {}
    attention_count = 0
    
    for job in jobs:
        status = job.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if job.get("needs_attention"):
            attention_count += 1
    
    return {
        "total": len(jobs),
        "new": status_counts.get(STATUS_NEW, 0),
        "applied": status_counts.get(STATUS_APPLIED, 0),
        "interview": status_counts.get(STATUS_INTERVIEW, 0),
        "offer": status_counts.get(STATUS_OFFER, 0),
        "rejected": status_counts.get(STATUS_REJECTED, 0),
        "withdrawn": status_counts.get(STATUS_WITHDRAWN, 0),
        "closed": status_counts.get(STATUS_CLOSED, 0),
        "excluded": status_counts.get(STATUS_EXCLUDED, 0),
        "needs_attention": attention_count,
        "status_breakdown": status_counts,
    }


# ============ Migration Helper ============

def migrate_from_old_storage() -> dict:
    """
    Migrate from old 4-file structure to new single file.
    Call once during upgrade.
    """
    old_files = {
        "new": DATA_DIR / "jobs_new.json",
        "pipeline": DATA_DIR / "jobs_pipeline.json",
        "archive": DATA_DIR / "jobs_archive.json",
        "excluded": DATA_DIR / "jobs_excluded.json",
    }
    
    migrated = {"total": 0, "by_source": {}}
    all_jobs = []
    seen_ids = set()
    
    for source, path in old_files.items():
        if not path.exists():
            continue
        
        try:
            with path.open("r", encoding="utf-8") as f:
                jobs = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        
        count = 0
        for job in jobs:
            job_id = job.get("id")
            if not job_id or job_id in seen_ids:
                continue
            
            # Normalize status to lowercase
            status = job.get("status", "New").lower()
            if status == "new" and source == "excluded":
                status = STATUS_EXCLUDED
            
            job["status"] = status
            all_jobs.append(job)
            seen_ids.add(job_id)
            count += 1
        
        migrated["by_source"][source] = count
        migrated["total"] += count
    
    if all_jobs:
        _save_jobs(all_jobs)
    
    return migrated
