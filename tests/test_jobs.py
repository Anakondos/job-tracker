import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parent.parent))

from main import app

client = TestClient(app)


def test_get_jobs_no_filters():
    response = client.get("/jobs")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data


def test_filter_state_ca():
    response = client.get("/jobs?state=CA")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    for job in data["jobs"]:
        loc = job.get("location", "")
        if loc:
            assert "ca" in loc.lower()


def test_filter_city_austin():
    response = client.get("/jobs?city=Austin")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    for job in data["jobs"]:
        loc = job.get("location", "")
        if loc:
            assert "austin" in loc.lower()


def test_filter_state_city_combination():
    response = client.get("/jobs?state=CA&city=San%20Francisco")
    assert response.status_code == 200
    data = response.json()
    assert "jobs" in data
    for job in data["jobs"]:
        loc = job.get("location", "")
        if loc:
            loc_lower = loc.lower()
            assert "ca" in loc_lower and "san francisco" in loc_lower

