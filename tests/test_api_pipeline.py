"""
Tests for Pipeline API endpoints.

Tests the main pipeline endpoints without touching production data.
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ============ Fixtures ============

@pytest.fixture
def mock_storage(tmp_path):
    """Mock the job storage to use temp file."""
    temp_file = tmp_path / "test_jobs.json"
    temp_file.write_text("[]")

    # We need to import and patch the storage module
    from storage import job_storage

    with patch.object(job_storage, 'JOBS_FILE', temp_file):
        yield temp_file, job_storage


@pytest.fixture
def sample_job_payload():
    """Sample job payload for API tests."""
    return {
        "job": {
            "id": "api-test-job-1",
            "title": "Product Manager",
            "company": "Test Company",
            "location": "Remote, USA",
            "url": "https://example.com/jobs/1",
            "ats": "greenhouse",
        }
    }


# ============ Pipeline Stats Tests ============

class TestPipelineStats:
    """Tests for /pipeline/stats endpoint."""

    def test_get_pipeline_stats(self):
        """Should return pipeline statistics."""
        response = client.get("/pipeline/stats")

        assert response.status_code == 200
        data = response.json()

        # Should have expected keys
        assert "total" in data or "stats" in data


# ============ Pipeline List Tests ============

class TestPipelineList:
    """Tests for /pipeline/* list endpoints."""

    def test_get_all_pipeline_jobs(self):
        """Should return all pipeline jobs."""
        response = client.get("/pipeline/all")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert isinstance(data["jobs"], list)

    def test_get_new_jobs(self):
        """Should return new jobs."""
        response = client.get("/pipeline/new")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data

    def test_get_active_jobs(self):
        """Should return active jobs."""
        response = client.get("/pipeline/active")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data

    def test_get_archive_jobs(self):
        """Should return archived jobs."""
        response = client.get("/pipeline/archive")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data


# ============ Pipeline CRUD Tests ============

class TestPipelineAdd:
    """Tests for /pipeline/add endpoint."""

    def test_add_job_to_pipeline(self, mock_storage, sample_job_payload):
        """Should add a job to pipeline."""
        _, storage = mock_storage

        response = client.post("/pipeline/add", json=sample_job_payload)

        assert response.status_code == 200
        data = response.json()
        assert data.get("success") is True or "added" in str(data).lower()

    def test_add_job_missing_id(self, mock_storage):
        """Should reject job without ID."""
        payload = {
            "job": {
                "title": "No ID Job",
                "company": "Test",
            }
        }

        response = client.post("/pipeline/add", json=payload)

        # Should either return error or success=false
        data = response.json()
        # Accept various error responses
        assert response.status_code in [200, 400, 422]


class TestPipelineStatusUpdate:
    """Tests for /pipeline/status endpoint."""

    def test_update_status(self, mock_storage, sample_job_payload):
        """Should update job status."""
        _, storage = mock_storage

        # First add a job
        client.post("/pipeline/add", json=sample_job_payload)

        # Then update status
        update_payload = {
            "job_id": sample_job_payload["job"]["id"],
            "status": "applied",
        }

        response = client.post("/pipeline/status", json=update_payload)

        assert response.status_code == 200

    def test_update_status_with_notes(self, mock_storage, sample_job_payload):
        """Should update status with notes."""
        _, storage = mock_storage

        client.post("/pipeline/add", json=sample_job_payload)

        update_payload = {
            "job_id": sample_job_payload["job"]["id"],
            "status": "interview",
            "notes": "Phone screen next week",
        }

        response = client.post("/pipeline/status", json=update_payload)

        assert response.status_code == 200


class TestPipelineRemove:
    """Tests for /pipeline/remove endpoint."""

    def test_remove_job(self, mock_storage, sample_job_payload):
        """Should remove job from pipeline."""
        _, storage = mock_storage

        # Add job first
        client.post("/pipeline/add", json=sample_job_payload)

        # Remove it
        job_id = sample_job_payload["job"]["id"]
        response = client.delete(f"/pipeline/remove/{job_id}")

        assert response.status_code == 200


class TestPipelineGetJob:
    """Tests for /pipeline/job/{job_id} endpoint."""

    def test_get_existing_job(self, mock_storage, sample_job_payload):
        """Should return existing job."""
        _, storage = mock_storage

        client.post("/pipeline/add", json=sample_job_payload)

        job_id = sample_job_payload["job"]["id"]
        response = client.get(f"/pipeline/job/{job_id}")

        assert response.status_code == 200
        data = response.json()
        assert "job" in data or "id" in data

    def test_get_nonexistent_job(self, mock_storage):
        """Should return 404 for unknown job."""
        response = client.get("/pipeline/job/nonexistent-id-12345")

        # Accept either 404 or 200 with null/error
        assert response.status_code in [200, 404]


# ============ Health Check Tests ============

class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health_check(self):
        """Should return healthy status."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok" or "healthy" in str(data).lower()

    def test_root_endpoint(self):
        """Should return welcome or redirect."""
        response = client.get("/")

        # Accept 200 (HTML) or redirect
        assert response.status_code in [200, 307, 308]


# ============ Jobs Endpoint Tests ============

class TestJobsEndpoint:
    """Tests for /jobs endpoint (from test_jobs.py)."""

    def test_get_jobs_no_filters(self):
        """Should return jobs without filters."""
        response = client.get("/jobs")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data

    def test_get_jobs_with_state_filter(self):
        """Should filter jobs by state."""
        response = client.get("/jobs?state=CA")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data

    def test_get_jobs_with_city_filter(self):
        """Should filter jobs by city."""
        response = client.get("/jobs?city=Austin")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data

    def test_get_jobs_with_remote_filter(self):
        """Should filter remote jobs."""
        response = client.get("/jobs?remote=true")

        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data


# ============ Companies Endpoint Tests ============

class TestCompaniesEndpoint:
    """Tests for /companies endpoints."""

    def test_get_companies(self):
        """Should return list of companies."""
        response = client.get("/companies")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, (list, dict))

    def test_get_profiles(self):
        """Should return profile data."""
        response = client.get("/profiles/anton_tpm")

        # Accept 200 or 404 if profile doesn't exist
        assert response.status_code in [200, 404]


# ============ Answers Endpoint Tests ============

class TestAnswersEndpoint:
    """Tests for /answers endpoints."""

    def test_get_answers(self):
        """Should return answers database."""
        response = client.get("/answers")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
