# storage/__init__.py
from .pipeline_storage import (
    # Statuses
    STATUS_NEW,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_OFFER,
    STATUS_REJECTED,
    STATUS_WITHDRAWN,
    STATUS_CLOSED,
    PIPELINE_STATUSES,
    ARCHIVE_STATUSES,
    ACTIVE_STATUSES,
    
    # Load functions
    load_new_jobs,
    load_pipeline_jobs,
    load_archive_jobs,
    load_excluded_jobs,
    load_all_known_jobs,
    get_all_job_ids,
    
    # Save functions
    save_new_jobs,
    save_pipeline_jobs,
    save_archive_jobs,
    save_excluded_jobs,
    
    # Operations
    add_new_job,
    update_job_status,
    exclude_job,
    update_last_seen,
    mark_missing_jobs,
    get_job_by_id,
    get_pipeline_stats,
)
