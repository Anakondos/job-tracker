#!/usr/bin/env python3
"""
Sync data from DEV to PROD.
- Merges new companies (doesn't overwrite existing)
- Merges new jobs (preserves PROD statuses)
"""

import json
from pathlib import Path
from datetime import datetime, timezone

DEV_DIR = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/job-tracker-dev")
PROD_DIR = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/job-tracker")


def sync_companies():
    """Merge new companies from DEV to PROD"""
    dev_file = DEV_DIR / "data/companies.json"
    prod_file = PROD_DIR / "data/companies.json"
    
    dev_companies = json.load(open(dev_file))
    prod_companies = json.load(open(prod_file)) if prod_file.exists() else []
    
    # Build PROD index by id
    prod_ids = {c.get("id") for c in prod_companies}
    
    # Add new companies from DEV
    added = 0
    for company in dev_companies:
        if company.get("id") not in prod_ids:
            prod_companies.append(company)
            added += 1
            print(f"  + Company: {company.get('name')}")
    
    # Save
    with open(prod_file, "w") as f:
        json.dump(prod_companies, f, indent=2, ensure_ascii=False)
    
    return {"added": added, "total": len(prod_companies)}


def sync_jobs():
    """Merge new jobs from DEV to PROD, preserving PROD statuses"""
    dev_file = DEV_DIR / "data/jobs.json"
    prod_file = PROD_DIR / "data/jobs.json"
    
    dev_jobs = json.load(open(dev_file)) if dev_file.exists() else []
    prod_jobs = json.load(open(prod_file)) if prod_file.exists() else []
    
    # Build PROD index by id, preserving status info
    prod_by_id = {j.get("id"): j for j in prod_jobs}
    
    added = 0
    updated = 0
    
    for job in dev_jobs:
        job_id = job.get("id")
        if not job_id:
            continue
            
        if job_id not in prod_by_id:
            # New job - add to PROD
            prod_jobs.append(job)
            added += 1
            print(f"  + Job: {job.get('title')} @ {job.get('company')}")
        else:
            # Existing job - update fields but PRESERVE status
            prod_job = prod_by_id[job_id]
            
            # Fields to preserve from PROD
            preserved = {
                "status": prod_job.get("status"),
                "status_history": prod_job.get("status_history"),
                "notes": prod_job.get("notes"),
                "application_status": prod_job.get("application_status"),
            }
            
            # Update other fields from DEV
            for key, value in job.items():
                if key not in preserved:
                    prod_job[key] = value
            
            # Restore preserved fields
            for key, value in preserved.items():
                if value is not None:
                    prod_job[key] = value
            
            updated += 1
    
    # Save
    with open(prod_file, "w") as f:
        json.dump(prod_jobs, f, indent=2, ensure_ascii=False)
    
    return {"added": added, "updated": updated, "total": len(prod_jobs)}


def main():
    print("=" * 50)
    print("Syncing DEV → PROD")
    print("=" * 50)
    
    print("\n📁 Companies:")
    companies_result = sync_companies()
    print(f"   Added: {companies_result['added']}, Total: {companies_result['total']}")
    
    print("\n📋 Jobs:")
    jobs_result = sync_jobs()
    print(f"   Added: {jobs_result['added']}, Updated: {jobs_result['updated']}, Total: {jobs_result['total']}")
    
    print("\n✅ Sync complete!")
    
    return {
        "companies": companies_result,
        "jobs": jobs_result,
        "synced_at": datetime.now(timezone.utc).isoformat()
    }


if __name__ == "__main__":
    main()
