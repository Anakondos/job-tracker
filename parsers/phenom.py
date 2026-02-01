"""
Phenom ATS Parser
Works with Phenom People career sites (e.g., Cisco, Intel)
Uses the /widgets endpoint with refineSearch ddoKey
"""
import requests
import re
from typing import List, Dict, Optional
from urllib.parse import urlparse


def fetch_phenom_jobs(company: str, base_url: str) -> List[Dict]:
    """
    Fetch jobs from Phenom ATS system.

    Args:
        company: Company name
        base_url: Base URL of the company's careers site (e.g., https://careers.cisco.com)

    Returns:
        List of job dictionaries
    """
    jobs = []

    try:
        parsed_url = urlparse(base_url)

        # Build widgets URL
        widgets_url = f"{parsed_url.scheme}://{parsed_url.netloc}/widgets"

        # Common headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Origin': f"{parsed_url.scheme}://{parsed_url.netloc}",
            'Referer': f"{parsed_url.scheme}://{parsed_url.netloc}/global/en/search-results"
        }

        # Phenom uses refineSearch endpoint with pagination
        page_size = 100
        offset = 0
        total_hits = None

        session = requests.Session()

        while True:
            payload = {
                "lang": "en_global",
                "siteType": "external",
                "deviceType": "desktop",
                "country": "global",
                "ddoKey": "refineSearch",
                "sortBy": "",
                "from": offset,
                "jobs": True,
                "counts": True,
                "all_fields": ["category", "country", "state", "city", "type", "seniority"],
                "size": page_size,
                "clearAll": False,
                "jdsource": "facets",
                "locationData": {}
            }

            try:
                response = session.post(widgets_url, headers=headers, json=payload, timeout=30)

                if response.status_code != 200:
                    print(f"Error: HTTP {response.status_code}")
                    break

                data = response.json()

                # Extract jobs from refineSearch response
                refine_data = data.get("refineSearch", {})
                jobs_data = refine_data.get("data", {}).get("jobs", [])

                if total_hits is None:
                    total_hits = refine_data.get("totalHits", 0)

                if not jobs_data:
                    break

                # Parse each job
                for job_data in jobs_data:
                    job = _parse_job(job_data, parsed_url.netloc, company)
                    if job:
                        jobs.append(job)

                # Check if we got all jobs
                offset += len(jobs_data)
                if offset >= total_hits or len(jobs_data) < page_size:
                    break

            except Exception as e:
                print(f"Error fetching page at offset {offset}: {e}")
                break

    except Exception as e:
        print(f"Error fetching jobs for {company}: {e}")

    return jobs


def _parse_job(job_data: dict, domain: str, company: str) -> Optional[Dict]:
    """Parse a single job from Phenom API response."""
    try:
        # Extract job ID - Phenom uses reqId or jobId
        job_id = (job_data.get("reqId") or
                 job_data.get("jobId") or
                 job_data.get("jobSeqNo", ""))

        if not job_id:
            return None

        # Extract title
        title = job_data.get("title", "")
        if not title:
            return None

        # Extract location - can be single or multi-location
        location = job_data.get("location", "")
        if not location:
            # Try multi_location array
            multi_loc = job_data.get("multi_location", [])
            if multi_loc:
                location = multi_loc[0] if isinstance(multi_loc, list) else str(multi_loc)
            else:
                # Build from city, state, country
                parts = []
                if job_data.get("city"):
                    parts.append(job_data["city"])
                if job_data.get("state"):
                    parts.append(job_data["state"])
                if job_data.get("country"):
                    parts.append(job_data["country"])
                location = ", ".join(parts)

        # Extract department/category
        department = job_data.get("category", "")
        if not department:
            multi_cat = job_data.get("multi_category", [])
            if multi_cat:
                department = multi_cat[0] if isinstance(multi_cat, list) else str(multi_cat)

        # Build job URL - Phenom typically uses /global/en/job/<jobSeqNo> format
        job_seq = job_data.get("jobSeqNo", job_id)
        job_url = f"https://{domain}/global/en/job/{job_seq}"

        # If there's an applyUrl, extract the base job URL
        if job_data.get("applyUrl"):
            # applyUrl is typically for Workday, but we want the Phenom page
            pass

        # Extract dates
        posted_date = job_data.get("postedDate", "")
        created_date = job_data.get("dateCreated", "")

        # Extract job type and remote status
        job_type = job_data.get("type", "")  # "Full time", etc.
        remote_type = job_data.get("RemoteType", "")  # "Hybrid", "Remote", etc.

        return {
            "company": company,
            "ats": "phenom",
            "ats_job_id": str(job_id),
            "title": title,
            "location": location.strip() if location else "",
            "department": department,
            "url": job_url,
            "first_published": posted_date or created_date,
            "updated_at": created_date or posted_date,
            "job_type": job_type,
            "remote_type": remote_type,
        }

    except Exception as e:
        return None


# List of known Phenom companies with their career site URLs
PHENOM_COMPANIES = {
    "cisco": "https://careers.cisco.com",
    # Add more companies here as they're discovered
}


def get_phenom_url(company_name: str) -> Optional[str]:
    """Get the Phenom career site URL for a company."""
    return PHENOM_COMPANIES.get(company_name.lower())


if __name__ == "__main__":
    # Test with Cisco
    print("Testing Phenom parser with Cisco...")
    jobs = fetch_phenom_jobs("Cisco", "https://careers.cisco.com")
    print(f"Found {len(jobs)} jobs")

    if jobs:
        print("\nFirst 5 jobs:")
        for job in jobs[:5]:
            print(f"  - {job['title']}")
            print(f"    Location: {job['location']}")
            print(f"    Department: {job['department']}")
            print(f"    ID: {job['ats_job_id']}")
            print()
    else:
        print("No jobs found - check API response")
