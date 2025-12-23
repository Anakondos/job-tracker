# storage/pipeline_storage.py
"""
Job Pipeline Storage System

Files:
- data/jobs_new.json       - Inbox: новые подходящие вакансии
- data/jobs_pipeline.json  - Активный pipeline (Applied, Interview, etc.)
- data/jobs_archive.json   - История (Rejected, Offer, Withdrawn)
- data/jobs_excluded.json  - Скрытые/неподходящие
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict

DATA_DIR = Path(__file__).parent.parent / "data"

# Статусы
STATUS_NEW = "New"
STATUS_APPLIED = "Applied"
STATUS_INTERVIEW = "Interview"
STATUS_OFFER = "Offer"
STATUS_REJECTED = "Rejected"
STATUS_WITHDRAWN = "Withdrawn"
STATUS_CLOSED = "Closed"  # Исчезла с ATS

# Группы статусов
PIPELINE_STATUSES = {STATUS_APPLIED, STATUS_INTERVIEW, STATUS_CLOSED}
ARCHIVE_STATUSES = {STATUS_REJECTED, STATUS_OFFER, STATUS_WITHDRAWN}
ACTIVE_STATUSES = {STATUS_APPLIED, STATUS_INTERVIEW}  # Требуют внимания если Closed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_json(path: Path, data: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============ File Paths ============

def _new_file() -> Path:
    return DATA_DIR / "jobs_new.json"

def _pipeline_file() -> Path:
    return DATA_DIR / "jobs_pipeline.json"

def _archive_file() -> Path:
    return DATA_DIR / "jobs_archive.json"

def _excluded_file() -> Path:
    return DATA_DIR / "jobs_excluded.json"


# ============ Load Functions ============

def load_new_jobs() -> List[dict]:
    return _load_json(_new_file())

def load_pipeline_jobs() -> List[dict]:
    return _load_json(_pipeline_file())

def load_archive_jobs() -> List[dict]:
    return _load_json(_archive_file())

def load_excluded_jobs() -> List[dict]:
    return _load_json(_excluded_file())

def load_all_known_jobs() -> List[dict]:
    """Load all jobs from all storages"""
    return load_new_jobs() + load_pipeline_jobs() + load_archive_jobs() + load_excluded_jobs()


def get_all_job_ids() -> set:
    """Get set of all known job IDs"""
    all_jobs = load_all_known_jobs()
    return {j.get("id") for j in all_jobs if j.get("id")}


# ============ Save Functions ============

def save_new_jobs(jobs: list):
    _save_json(_new_file(), jobs)

def save_pipeline_jobs(jobs: list):
    _save_json(_pipeline_file(), jobs)

def save_archive_jobs(jobs: list):
    _save_json(_archive_file(), jobs)

def save_excluded_jobs(jobs: list):
    _save_json(_excluded_file(), jobs)


# ============ Job Operations ============

def add_new_job(job: dict) -> bool:
    """
    Add a new job to the inbox.
    Returns True if added, False if already exists.
    """
    job_id = job.get("id")
    if not job_id:
        return False
    
    # Check if already known
    known_ids = get_all_job_ids()
    if job_id in known_ids:
        return False
    
    # Prepare job record
    now = _now_iso()
    job_record = {
        **job,
        "status": STATUS_NEW,
        "status_history": [{"status": STATUS_NEW, "date": now}],
        "first_seen": now,
        "last_seen": now,
        "is_active_on_ats": True,
        "needs_attention": False,
        "notes": "",
    }
    
    new_jobs = load_new_jobs()
    new_jobs.append(job_record)
    save_new_jobs(new_jobs)
    
    return True


def update_job_status(job_id: str, new_status: str, notes: str = "") -> Optional[dict]:
    """
    Update job status and move between storages if needed.
    Returns updated job or None if not found.
    """
    now = _now_iso()
    
    # Find job in all storages
    for storage_name, load_fn, save_fn in [
        ("new", load_new_jobs, save_new_jobs),
        ("pipeline", load_pipeline_jobs, save_pipeline_jobs),
        ("archive", load_archive_jobs, save_archive_jobs),
    ]:
        jobs = load_fn()
        job_idx = None
        job = None
        
        for i, j in enumerate(jobs):
            if j.get("id") == job_id:
                job_idx = i
                job = j
                break
        
        if job is None:
            continue
        
        # Update job
        old_status = job.get("status", STATUS_NEW)
        job["status"] = new_status
        job["status_history"] = job.get("status_history", [])
        job["status_history"].append({"status": new_status, "date": now})
        job["updated_at"] = now
        
        if notes:
            job["notes"] = notes
        
        # Clear attention flag if status changed
        if new_status != STATUS_CLOSED:
            job["needs_attention"] = False
        
        # Remove from current storage
        jobs.pop(job_idx)
        save_fn(jobs)
        
        # Add to appropriate storage
        if new_status == STATUS_NEW:
            new_jobs = load_new_jobs()
            new_jobs.append(job)
            save_new_jobs(new_jobs)
        elif new_status in PIPELINE_STATUSES:
            pipeline = load_pipeline_jobs()
            pipeline.append(job)
            save_pipeline_jobs(pipeline)
        elif new_status in ARCHIVE_STATUSES:
            archive = load_archive_jobs()
            archive.append(job)
            save_archive_jobs(archive)
        else:
            # Default to pipeline
            pipeline = load_pipeline_jobs()
            pipeline.append(job)
            save_pipeline_jobs(pipeline)
        
        return job
    
    return None


def exclude_job(job_id: str, reason: str = "manual") -> Optional[dict]:
    """
    Move job to excluded (hidden) list.
    """
    now = _now_iso()
    
    # Find and remove from new jobs
    new_jobs = load_new_jobs()
    job = None
    
    for i, j in enumerate(new_jobs):
        if j.get("id") == job_id:
            job = new_jobs.pop(i)
            break
    
    if job is None:
        return None
    
    save_new_jobs(new_jobs)
    
    # Add to excluded
    job["excluded_at"] = now
    job["exclude_reason"] = reason
    
    excluded = load_excluded_jobs()
    excluded.append(job)
    save_excluded_jobs(excluded)
    
    return job


def update_last_seen(job_id: str, is_active: bool = True) -> bool:
    """
    Update last_seen timestamp for a job.
    Called during parsing to mark job as still active on ATS.
    """
    now = _now_iso()
    
    for storage_name, load_fn, save_fn in [
        ("new", load_new_jobs, save_new_jobs),
        ("pipeline", load_pipeline_jobs, save_pipeline_jobs),
    ]:
        jobs = load_fn()
        
        for job in jobs:
            if job.get("id") == job_id:
                job["last_seen"] = now
                job["is_active_on_ats"] = is_active
                save_fn(jobs)
                return True
    
    return False


def mark_missing_jobs(active_job_ids: set, days_threshold: int = 3) -> List[dict]:
    """
    Mark pipeline jobs as Closed if they haven't been seen for days_threshold days.
    Returns list of jobs that need attention.
    
    Called after parsing to detect removed positions.
    """
    now = datetime.now(timezone.utc)
    needs_attention = []
    
    pipeline = load_pipeline_jobs()
    changed = False
    
    for job in pipeline:
        job_id = job.get("id")
        
        if job_id in active_job_ids:
            # Still active
            job["is_active_on_ats"] = True
            job["needs_attention"] = False
            changed = True
            continue
        
        # Not in active list
        job["is_active_on_ats"] = False
        
        # Check if in active status and needs attention
        if job.get("status") in ACTIVE_STATUSES:
            last_seen = job.get("last_seen", "")
            if last_seen:
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
        save_pipeline_jobs(pipeline)
    
    return needs_attention


def get_job_by_id(job_id: str) -> Optional[dict]:
    """Find job by ID in any storage"""
    for load_fn in [load_new_jobs, load_pipeline_jobs, load_archive_jobs, load_excluded_jobs]:
        for job in load_fn():
            if job.get("id") == job_id:
                return job
    return None


def get_pipeline_stats() -> dict:
    """Get summary statistics"""
    new_jobs = load_new_jobs()
    pipeline = load_pipeline_jobs()
    archive = load_archive_jobs()
    
    # Count by status in pipeline
    status_counts = {}
    attention_count = 0
    
    for job in pipeline:
        status = job.get("status", "Unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if job.get("needs_attention"):
            attention_count += 1
    
    # Count outcomes in archive
    outcomes = {}
    for job in archive:
        status = job.get("status", "Unknown")
        outcomes[status] = outcomes.get(status, 0) + 1
    
    return {
        "new_count": len(new_jobs),
        "pipeline_count": len(pipeline),
        "archive_count": len(archive),
        "status_breakdown": status_counts,
        "needs_attention": attention_count,
        "outcomes": outcomes,
    }
