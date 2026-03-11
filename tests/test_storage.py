"""
Tests for job_storage.py

Tests the core storage functionality without touching the real data file.
Uses a temporary file for isolation.
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage import job_storage


# ============ Fixtures ============

@pytest.fixture
def temp_jobs_file(tmp_path):
    """Create a temporary jobs file for testing."""
    temp_file = tmp_path / "test_jobs.json"
    temp_file.write_text("[]")

    # Patch the JOBS_FILE constant
    with patch.object(job_storage, 'JOBS_FILE', temp_file):
        yield temp_file


@pytest.fixture
def sample_job():
    """Sample job for testing."""
    return {
        "id": "test-job-123",
        "title": "Senior Software Engineer",
        "company": "Test Corp",
        "location": "San Francisco, CA",
        "url": "https://example.com/jobs/123",
        "ats": "greenhouse",
    }


@pytest.fixture
def sample_jobs():
    """Multiple sample jobs for testing."""
    return [
        {
            "id": "job-1",
            "title": "Frontend Developer",
            "company": "Company A",
            "location": "New York, NY",
            "ats": "lever",
        },
        {
            "id": "job-2",
            "title": "Backend Developer",
            "company": "Company B",
            "location": "Austin, TX",
            "ats": "greenhouse",
        },
        {
            "id": "job-3",
            "title": "DevOps Engineer",
            "company": "Company C",
            "location": "Remote",
            "ats": "workday",
        },
    ]


# ============ Basic CRUD Tests ============

class TestAddJob:
    """Tests for add_job function."""

    def test_add_new_job(self, temp_jobs_file, sample_job):
        """Should add a new job successfully."""
        result = job_storage.add_job(sample_job)

        assert result is True

        jobs = job_storage.get_all_jobs()
        assert len(jobs) == 1
        assert jobs[0]["id"] == sample_job["id"]
        assert jobs[0]["title"] == sample_job["title"]
        assert jobs[0]["status"] == job_storage.STATUS_NEW

    def test_add_job_with_custom_status(self, temp_jobs_file, sample_job):
        """Should add job with specified status."""
        result = job_storage.add_job(sample_job, status=job_storage.STATUS_APPLIED)

        assert result is True
        job = job_storage.get_job_by_id(sample_job["id"])
        assert job["status"] == job_storage.STATUS_APPLIED

    def test_add_duplicate_job(self, temp_jobs_file, sample_job):
        """Should not add duplicate job."""
        job_storage.add_job(sample_job)
        result = job_storage.add_job(sample_job)

        assert result is False
        assert len(job_storage.get_all_jobs()) == 1

    def test_add_job_without_id(self, temp_jobs_file):
        """Should reject job without ID."""
        job = {"title": "No ID Job", "company": "Test"}
        result = job_storage.add_job(job)

        assert result is False
        assert len(job_storage.get_all_jobs()) == 0

    def test_add_job_creates_metadata(self, temp_jobs_file, sample_job):
        """Should add metadata fields to job."""
        job_storage.add_job(sample_job)
        job = job_storage.get_job_by_id(sample_job["id"])

        assert "first_seen" in job
        assert "last_seen" in job
        assert "status_history" in job
        assert "is_active_on_ats" in job
        assert job["is_active_on_ats"] is True


class TestAddJobsBulk:
    """Tests for add_jobs_bulk function."""

    def test_add_multiple_jobs(self, temp_jobs_file, sample_jobs):
        """Should add multiple jobs at once."""
        count = job_storage.add_jobs_bulk(sample_jobs)

        assert count == 3
        assert len(job_storage.get_all_jobs()) == 3

    def test_bulk_add_skips_duplicates(self, temp_jobs_file, sample_jobs):
        """Should skip duplicates in bulk add."""
        job_storage.add_job(sample_jobs[0])
        count = job_storage.add_jobs_bulk(sample_jobs)

        assert count == 2  # Only 2 new jobs added
        assert len(job_storage.get_all_jobs()) == 3

    def test_bulk_add_empty_list(self, temp_jobs_file):
        """Should handle empty list."""
        count = job_storage.add_jobs_bulk([])
        assert count == 0


class TestGetJobs:
    """Tests for get functions."""

    def test_get_all_jobs(self, temp_jobs_file, sample_jobs):
        """Should return all jobs."""
        job_storage.add_jobs_bulk(sample_jobs)
        jobs = job_storage.get_all_jobs()

        assert len(jobs) == 3

    def test_get_job_by_id(self, temp_jobs_file, sample_job):
        """Should find job by ID."""
        job_storage.add_job(sample_job)
        job = job_storage.get_job_by_id(sample_job["id"])

        assert job is not None
        assert job["id"] == sample_job["id"]

    def test_get_job_by_id_not_found(self, temp_jobs_file):
        """Should return None for unknown ID."""
        job = job_storage.get_job_by_id("nonexistent-id")
        assert job is None

    def test_get_jobs_by_status(self, temp_jobs_file, sample_jobs):
        """Should filter jobs by status."""
        job_storage.add_jobs_bulk(sample_jobs)
        job_storage.update_status("job-1", job_storage.STATUS_APPLIED)

        new_jobs = job_storage.get_jobs_by_status(job_storage.STATUS_NEW)
        applied_jobs = job_storage.get_jobs_by_status(job_storage.STATUS_APPLIED)

        assert len(new_jobs) == 2
        assert len(applied_jobs) == 1

    def test_get_active_jobs(self, temp_jobs_file, sample_jobs):
        """Should return only active jobs."""
        job_storage.add_jobs_bulk(sample_jobs)
        job_storage.update_status("job-1", job_storage.STATUS_REJECTED)

        active = job_storage.get_active_jobs()
        assert len(active) == 2
        assert all(j["status"] in job_storage.ACTIVE_STATUSES for j in active)

    def test_get_archive_jobs(self, temp_jobs_file, sample_jobs):
        """Should return only archived jobs."""
        job_storage.add_jobs_bulk(sample_jobs)
        job_storage.update_status("job-1", job_storage.STATUS_REJECTED)
        job_storage.update_status("job-2", job_storage.STATUS_OFFER)

        archive = job_storage.get_archive_jobs()
        assert len(archive) == 2


class TestUpdateStatus:
    """Tests for update_status function."""

    def test_update_status(self, temp_jobs_file, sample_job):
        """Should update job status."""
        job_storage.add_job(sample_job)
        result = job_storage.update_status(sample_job["id"], job_storage.STATUS_APPLIED)

        assert result is not None
        assert result["status"] == job_storage.STATUS_APPLIED

    def test_update_status_adds_history(self, temp_jobs_file, sample_job):
        """Should add entry to status history."""
        job_storage.add_job(sample_job)
        job_storage.update_status(sample_job["id"], job_storage.STATUS_APPLIED)
        job_storage.update_status(sample_job["id"], job_storage.STATUS_INTERVIEW)

        job = job_storage.get_job_by_id(sample_job["id"])
        assert len(job["status_history"]) == 3  # new + applied + interview

    def test_update_status_with_notes(self, temp_jobs_file, sample_job):
        """Should save notes with status update."""
        job_storage.add_job(sample_job)
        job_storage.update_status(
            sample_job["id"],
            job_storage.STATUS_INTERVIEW,
            notes="Phone screen scheduled"
        )

        job = job_storage.get_job_by_id(sample_job["id"])
        assert job["notes"] == "Phone screen scheduled"

    def test_update_status_with_folder_path(self, temp_jobs_file, sample_job):
        """Should save folder path with status update."""
        job_storage.add_job(sample_job)
        job_storage.update_status(
            sample_job["id"],
            job_storage.STATUS_APPLIED,
            folder_path="/path/to/application"
        )

        job = job_storage.get_job_by_id(sample_job["id"])
        assert job["folder_path"] == "/path/to/application"

    def test_update_status_not_found(self, temp_jobs_file):
        """Should return None for unknown job."""
        result = job_storage.update_status("nonexistent", job_storage.STATUS_APPLIED)
        assert result is None


class TestRemoveJob:
    """Tests for remove_job function."""

    def test_remove_existing_job(self, temp_jobs_file, sample_job):
        """Should remove existing job."""
        job_storage.add_job(sample_job)
        result = job_storage.remove_job(sample_job["id"])

        assert result is True
        assert job_storage.get_job_by_id(sample_job["id"]) is None

    def test_remove_nonexistent_job(self, temp_jobs_file):
        """Should return False for unknown job."""
        result = job_storage.remove_job("nonexistent")
        assert result is False


class TestJobExists:
    """Tests for job_exists function."""

    def test_job_exists_true(self, temp_jobs_file, sample_job):
        """Should return True for existing job."""
        job_storage.add_job(sample_job)
        assert job_storage.job_exists(sample_job["id"]) is True

    def test_job_exists_false(self, temp_jobs_file):
        """Should return False for unknown job."""
        assert job_storage.job_exists("nonexistent") is False


class TestGetStats:
    """Tests for get_stats function."""

    def test_get_stats_empty(self, temp_jobs_file):
        """Should return zero stats for empty storage."""
        stats = job_storage.get_stats()

        assert stats["total"] == 0
        assert stats["new"] == 0
        assert stats["applied"] == 0

    def test_get_stats_with_jobs(self, temp_jobs_file, sample_jobs):
        """Should return correct stats."""
        job_storage.add_jobs_bulk(sample_jobs)
        job_storage.update_status("job-1", job_storage.STATUS_APPLIED)
        job_storage.update_status("job-2", job_storage.STATUS_INTERVIEW)

        stats = job_storage.get_stats()

        assert stats["total"] == 3
        assert stats["new"] == 1
        assert stats["applied"] == 1
        assert stats["interview"] == 1


# ============ Edge Cases ============

class TestEdgeCases:
    """Edge case tests."""

    def test_empty_storage_file(self, temp_jobs_file):
        """Should handle empty storage gracefully."""
        temp_jobs_file.write_text("")
        jobs = job_storage.get_all_jobs()
        assert jobs == []

    def test_corrupted_json(self, temp_jobs_file):
        """Should handle corrupted JSON gracefully."""
        temp_jobs_file.write_text("{invalid json")
        jobs = job_storage.get_all_jobs()
        assert jobs == []

    def test_nonexistent_file(self, tmp_path):
        """Should handle missing file gracefully."""
        missing_file = tmp_path / "nonexistent.json"
        with patch.object(job_storage, 'JOBS_FILE', missing_file):
            jobs = job_storage.get_all_jobs()
            assert jobs == []
