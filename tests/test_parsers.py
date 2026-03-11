"""
Tests for ATS parsers (Greenhouse, Lever, Workday).

These tests verify the parsing logic without making actual HTTP requests.
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers.greenhouse import fetch_greenhouse
from parsers.lever import fetch_lever
from parsers.workday_v2 import (
    parse_workday_url,
    fetch_workday_v2,
    _normalize_job,
    _parse_posted_on,
)


# ============ Greenhouse Parser Tests ============

class TestGreenhouseParser:
    """Tests for Greenhouse parser."""

    @pytest.fixture
    def mock_greenhouse_response(self):
        """Sample Greenhouse API response."""
        return {
            "jobs": [
                {
                    "id": 123456,
                    "title": "Senior Software Engineer",
                    "location": {"name": "San Francisco, CA"},
                    "departments": [{"name": "Engineering"}],
                    "absolute_url": "https://boards.greenhouse.io/company/jobs/123456",
                    "first_published": "2026-01-15T10:00:00Z",
                    "updated_at": "2026-01-20T15:30:00Z",
                },
                {
                    "id": 789012,
                    "title": "Product Manager",
                    "location": {"name": "Remote"},
                    "departments": [{"name": "Product"}],
                    "absolute_url": "https://boards.greenhouse.io/company/jobs/789012",
                    "first_published": "2026-01-10T08:00:00Z",
                    "updated_at": "2026-01-18T12:00:00Z",
                },
            ]
        }

    def test_parse_greenhouse_jobs(self, mock_greenhouse_response):
        """Should parse Greenhouse jobs correctly."""
        with patch('parsers.greenhouse.requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_greenhouse_response
            mock_get.return_value.raise_for_status = MagicMock()

            jobs = fetch_greenhouse("Test Company", "https://boards.greenhouse.io/testcompany")

            assert len(jobs) == 2
            assert jobs[0]["title"] == "Senior Software Engineer"
            assert jobs[0]["ats"] == "greenhouse"
            assert jobs[0]["company"] == "Test Company"
            assert jobs[0]["location"] == "San Francisco, CA"
            assert jobs[0]["department"] == "Engineering"

    def test_greenhouse_extracts_board_token(self, mock_greenhouse_response):
        """Should extract board token from URL correctly."""
        with patch('parsers.greenhouse.requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_greenhouse_response
            mock_get.return_value.raise_for_status = MagicMock()

            fetch_greenhouse("Test", "https://boards.greenhouse.io/mycompany")

            # Check that correct API URL was called
            call_url = mock_get.call_args[0][0]
            assert "mycompany" in call_url
            assert "boards-api.greenhouse.io" in call_url

    def test_greenhouse_handles_empty_response(self):
        """Should handle empty job list."""
        with patch('parsers.greenhouse.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {"jobs": []}
            mock_get.return_value.raise_for_status = MagicMock()

            jobs = fetch_greenhouse("Test", "https://boards.greenhouse.io/test")

            assert jobs == []

    def test_greenhouse_handles_missing_fields(self):
        """Should handle jobs with missing optional fields."""
        response = {
            "jobs": [
                {
                    "id": 111,
                    "title": "Engineer",
                    "location": {},  # Missing name
                    "departments": [],  # Empty
                    "absolute_url": "https://example.com",
                }
            ]
        }

        with patch('parsers.greenhouse.requests.get') as mock_get:
            mock_get.return_value.json.return_value = response
            mock_get.return_value.raise_for_status = MagicMock()

            jobs = fetch_greenhouse("Test", "https://boards.greenhouse.io/test")

            assert len(jobs) == 1
            assert jobs[0]["location"] == ""
            assert jobs[0]["department"] == ""


# ============ Lever Parser Tests ============

class TestLeverParser:
    """Tests for Lever parser."""

    @pytest.fixture
    def mock_lever_response(self):
        """Sample Lever API response."""
        return [
            {
                "id": "abc123",
                "text": "Frontend Developer",
                "categories": {
                    "location": "New York, NY",
                    "team": "Engineering",
                },
                "hostedUrl": "https://jobs.lever.co/company/abc123",
                "createdAt": 1705000000000,
            },
            {
                "id": "def456",
                "text": "Data Scientist",
                "categories": {
                    "location": "Remote",
                    "team": "Data",
                },
                "hostedUrl": "https://jobs.lever.co/company/def456",
                "createdAt": 1704500000000,
            },
        ]

    def test_parse_lever_jobs(self, mock_lever_response):
        """Should parse Lever jobs correctly."""
        with patch('parsers.lever.requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_lever_response
            mock_get.return_value.raise_for_status = MagicMock()

            jobs = fetch_lever("Test Company", "https://jobs.lever.co/testcompany")

            assert len(jobs) == 2
            assert jobs[0]["title"] == "Frontend Developer"
            assert jobs[0]["ats"] == "lever"
            assert jobs[0]["location"] == "New York, NY"
            assert jobs[0]["department"] == "Engineering"

    def test_lever_extracts_slug(self, mock_lever_response):
        """Should extract company slug from URL."""
        with patch('parsers.lever.requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_lever_response
            mock_get.return_value.raise_for_status = MagicMock()

            fetch_lever("Test", "https://jobs.lever.co/mycompany")

            call_url = mock_get.call_args[0][0]
            assert "mycompany" in call_url
            assert "api.lever.co" in call_url

    def test_lever_handles_empty_response(self):
        """Should handle empty job list."""
        with patch('parsers.lever.requests.get') as mock_get:
            mock_get.return_value.json.return_value = []
            mock_get.return_value.raise_for_status = MagicMock()

            jobs = fetch_lever("Test", "https://jobs.lever.co/test")

            assert jobs == []


# ============ Workday Parser Tests ============

class TestWorkdayUrlParser:
    """Tests for Workday URL parsing."""

    def test_parse_standard_url(self):
        """Should parse standard Workday URL."""
        url = "https://capitalone.wd12.myworkdayjobs.com/Capital_One"
        result = parse_workday_url(url)

        assert result["company"] == "capitalone"
        assert result["wd_instance"] == "wd12"
        assert result["board_name"] == "Capital_One"
        assert "api_url" in result

    def test_parse_url_with_locale(self):
        """Should parse URL with locale prefix."""
        url = "https://archgroup.wd1.myworkdayjobs.com/en-US/Careers"
        result = parse_workday_url(url)

        assert result["company"] == "archgroup"
        assert result["wd_instance"] == "wd1"
        assert result["board_name"] == "Careers"

    def test_parse_invalid_url(self):
        """Should raise error for invalid URL."""
        with pytest.raises(ValueError):
            parse_workday_url("https://invalid-url.com/jobs")

    def test_api_url_construction(self):
        """Should construct correct API URL."""
        url = "https://example.wd5.myworkdayjobs.com/ExternalCareers"
        result = parse_workday_url(url)

        expected_api = "https://example.wd5.myworkdayjobs.com/wday/cxs/example/ExternalCareers/jobs"
        assert result["api_url"] == expected_api


class TestWorkdayDateParsing:
    """Tests for Workday date parsing."""

    def test_parse_today(self):
        """Should parse 'Posted Today'."""
        result = _parse_posted_on("Posted Today")
        assert result is not None

    def test_parse_yesterday(self):
        """Should parse 'Posted Yesterday'."""
        result = _parse_posted_on("Posted Yesterday")
        assert result is not None

    def test_parse_days_ago(self):
        """Should parse 'Posted 5 Days Ago'."""
        result = _parse_posted_on("Posted 5 Days Ago")
        assert result is not None

    def test_parse_30_plus_days(self):
        """Should parse '30+ Days Ago'."""
        result = _parse_posted_on("Posted 30+ Days Ago")
        assert result is not None

    def test_parse_empty(self):
        """Should handle empty string."""
        result = _parse_posted_on("")
        assert result is None

    def test_parse_none(self):
        """Should handle None."""
        result = _parse_posted_on(None)
        assert result is None


class TestWorkdayJobNormalization:
    """Tests for Workday job normalization."""

    def test_normalize_full_job(self):
        """Should normalize job with all fields."""
        raw_job = {
            "title": "Software Engineer",
            "externalPath": "/job/software-engineer_123",
            "locationsText": "Austin, TX",
            "postedOn": "Posted 3 Days Ago",
            "bulletFields": ["REQ-123", "Full-time"],
            "timeType": "Full time",
        }

        result = _normalize_job(raw_job, "Test Corp", "https://test.wd1.myworkdayjobs.com/en-US/Careers")

        assert result["title"] == "Software Engineer"
        assert result["company"] == "Test Corp"
        assert result["ats"] == "workday"
        assert result["location"] == "Austin, TX"
        assert result["ats_job_id"] == "REQ-123"
        assert "job/software-engineer_123" in result["url"]

    def test_normalize_minimal_job(self):
        """Should handle job with minimal fields."""
        raw_job = {
            "title": "Engineer",
        }

        result = _normalize_job(raw_job, "Test", "https://example.com")

        assert result is not None
        assert result["title"] == "Engineer"

    def test_normalize_job_without_title(self):
        """Should return None for job without title."""
        raw_job = {
            "locationsText": "Remote",
        }

        result = _normalize_job(raw_job, "Test", "https://example.com")

        assert result is None


class TestWorkdayFetcher:
    """Tests for Workday job fetcher."""

    @pytest.fixture
    def mock_workday_response(self):
        """Sample Workday API response."""
        return {
            "total": 2,
            "jobPostings": [
                {
                    "title": "DevOps Engineer",
                    "externalPath": "/job/devops_001",
                    "locationsText": "Remote, USA",
                    "postedOn": "Posted Today",
                    "bulletFields": ["JOB-001"],
                    "timeType": "Full time",
                },
                {
                    "title": "Cloud Architect",
                    "externalPath": "/job/cloud_002",
                    "locationsText": "Seattle, WA",
                    "postedOn": "Posted Yesterday",
                    "bulletFields": ["JOB-002"],
                    "timeType": "Full time",
                },
            ],
        }

    def test_fetch_workday_jobs(self, mock_workday_response):
        """Should fetch and parse Workday jobs."""
        with patch('parsers.workday_v2.requests.post') as mock_post:
            mock_post.return_value.json.return_value = mock_workday_response
            mock_post.return_value.raise_for_status = MagicMock()

            jobs = fetch_workday_v2(
                company="Test Corp",
                board_url="https://test.wd1.myworkdayjobs.com/Careers",
                max_jobs=10,
            )

            assert len(jobs) == 2
            assert jobs[0]["title"] == "DevOps Engineer"
            assert jobs[0]["ats"] == "workday"

    def test_fetch_workday_pagination(self, mock_workday_response):
        """Should handle pagination correctly."""
        # First page
        page1 = {
            "total": 25,
            "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/{i}"} for i in range(20)],
        }
        # Second page (less than 20)
        page2 = {
            "total": 25,
            "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/{i}"} for i in range(20, 25)],
        }

        with patch('parsers.workday_v2.requests.post') as mock_post:
            mock_post.return_value.raise_for_status = MagicMock()
            mock_post.return_value.json.side_effect = [page1, page2]

            jobs = fetch_workday_v2(
                company="Test",
                board_url="https://test.wd1.myworkdayjobs.com/Careers",
            )

            assert len(jobs) == 25
            assert mock_post.call_count == 2

    def test_fetch_workday_max_jobs(self, mock_workday_response):
        """Should respect max_jobs limit."""
        response = {
            "total": 100,
            "jobPostings": [{"title": f"Job {i}", "externalPath": f"/job/{i}"} for i in range(20)],
        }

        with patch('parsers.workday_v2.requests.post') as mock_post:
            mock_post.return_value.json.return_value = response
            mock_post.return_value.raise_for_status = MagicMock()

            jobs = fetch_workday_v2(
                company="Test",
                board_url="https://test.wd1.myworkdayjobs.com/Careers",
                max_jobs=5,
            )

            assert len(jobs) == 5

    def test_fetch_workday_invalid_url(self):
        """Should handle invalid URL gracefully."""
        jobs = fetch_workday_v2(
            company="Test",
            board_url="https://invalid-url.com/jobs",
        )

        assert jobs == []
