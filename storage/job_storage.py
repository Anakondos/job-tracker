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
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Set
from utils.location_utils import normalize_job_location


DATA_DIR = Path(__file__).parent.parent / "data"
JOBS_FILE = DATA_DIR / "jobs_new.json"  # Unified with pipeline
REJECTED_FILE = DATA_DIR / "rejected_jobs.json"  # Memory of rejected/excluded job IDs

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


# ============ Rejected Jobs Memory ============
# Stores ats_job_id + title of jobs user marked as rejected/excluded/withdrawn.
# These jobs won't be re-added to pipeline on next parse cycle.

SKIP_STATUSES = {STATUS_REJECTED, STATUS_EXCLUDED, STATUS_WITHDRAWN}

def _load_rejected() -> dict:
    """Load rejected jobs memory: {ats_job_id: {title, company, date, reason}}"""
    if not REJECTED_FILE.exists():
        return {}
    try:
        with REJECTED_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_rejected(data: dict):
    """Save rejected jobs memory with atomic write + fsync (iCloud safe)"""
    REJECTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(REJECTED_FILE.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(REJECTED_FILE))
        dir_fd = os.open(str(REJECTED_FILE.parent), os.O_RDONLY)
        os.fsync(dir_fd)
        os.close(dir_fd)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def add_to_rejected(job: dict, reason: str = "excluded"):
    """Remember a job as rejected so it won't be re-added."""
    ats_job_id = job.get("ats_job_id") or job.get("id") or ""
    if not ats_job_id:
        return
    rejected = _load_rejected()
    rejected[str(ats_job_id)] = {
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "date": _now_iso(),
        "reason": reason,
    }
    _save_rejected(rejected)


def is_rejected(ats_job_id: str) -> bool:
    """Check if a job ID was previously rejected/excluded."""
    if not ats_job_id:
        return False
    rejected = _load_rejected()
    return str(ats_job_id) in rejected


def get_rejected_ids() -> set:
    """Get all rejected job IDs."""
    return set(_load_rejected().keys())


def remove_from_rejected(ats_job_id: str):
    """Remove a job from rejected memory (e.g., if user re-opens it)."""
    rejected = _load_rejected()
    if str(ats_job_id) in rejected:
        del rejected[str(ats_job_id)]
        _save_rejected(rejected)


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
    """Save all jobs to storage with atomic write + fsync (iCloud safe)"""
    JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(JOBS_FILE.parent), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(jobs, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(JOBS_FILE))
        # fsync directory to ensure rename is persisted
        dir_fd = os.open(str(JOBS_FILE.parent), os.O_RDONLY)
        os.fsync(dir_fd)
        os.close(dir_fd)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


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
    Returns True if added, False if already exists or was previously rejected.
    """
    job_id = job.get("id")
    if not job_id:
        return False

    # Check rejected memory - skip previously rejected/excluded jobs
    ats_job_id = job.get("ats_job_id") or ""
    if ats_job_id and is_rejected(str(ats_job_id)):
        return False

    jobs = _load_jobs()

    # Check if already exists
    if any(j.get("id") == job_id for j in jobs):
        return False
    
    # Normalize location (from title if needed)
    job = normalize_job_location(job)
    
    now = _now_iso()
    # Use original ATS date if available, otherwise use current time
    original_date = job.get("first_published") or job.get("updated_at") or now
    job_record = {
        **job,
        "status": status,
        "status_history": [{"status": status, "date": now}],
        "first_seen": original_date,
        "added_to_pipeline": now,
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
    Returns count of actually added jobs. Skips previously rejected/excluded jobs.
    """
    if not new_jobs:
        return 0

    jobs = _load_jobs()
    existing_ids = {j.get("id") for j in jobs}
    rejected_ids = get_rejected_ids()

    now = _now_iso()
    added = 0

    for job in new_jobs:
        job_id = job.get("id")
        if not job_id or job_id in existing_ids:
            continue

        # Skip previously rejected/excluded jobs
        ats_job_id = job.get("ats_job_id") or ""
        if ats_job_id and str(ats_job_id) in rejected_ids:
            continue
        
        # Normalize location
        job = normalize_job_location(job)
        
        # Use original ATS date if available, otherwise use current time
        original_date = job.get("first_published") or job.get("updated_at") or now
        job_record = {
            **job,
            "status": status,
            "status_history": [{"status": status, "date": now}],
            "first_seen": original_date,
            "added_to_pipeline": now,
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


def update_status(job_id: str, new_status: str, notes: str = "", folder_path: str = "", jd_summary: dict = None) -> Optional[dict]:
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
            if jd_summary:
                job["jd_summary"] = jd_summary
            
            # Clear attention flag unless closing
            if new_status != STATUS_CLOSED:
                job["needs_attention"] = False

            # Remember rejected/excluded/withdrawn jobs to prevent re-adding
            if new_status in SKIP_STATUSES:
                add_to_rejected(job, reason=new_status)
            elif old_status in SKIP_STATUSES and new_status not in SKIP_STATUSES:
                # If user re-opens a previously rejected job, remove from memory
                ats_jid = job.get("ats_job_id") or ""
                if ats_jid:
                    remove_from_rejected(str(ats_jid))

            _save_jobs(jobs)
            return job

    return None


def update_jd_summary(job_id: str, jd_summary: dict) -> bool:
    """
    Update job's jd_summary field.
    Returns True if successful.
    """
    jobs = _load_jobs()
    now = _now_iso()
    
    for job in jobs:
        if job.get("id") == job_id:
            job["jd_summary"] = jd_summary
            job["updated_at"] = now
            _save_jobs(jobs)
            return True
    
    return False


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
