#!/usr/bin/env python3
"""
ATS Discovery Tool - анализирует сайты для обнаружения API endpoints
и собирает данные для добавления поддержки новых ATS.

Использование:
    python tools/ats_discovery.py discover "https://careers.company.com"
    python tools/ats_discovery.py list
    python tools/ats_discovery.py generate phenom
"""

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse, urljoin

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_FILE = PROJECT_ROOT / "data" / "unsupported_ats.json"
PARSERS_DIR = PROJECT_ROOT / "parsers"


def load_unsupported_ats() -> Dict:
    """Load the unsupported ATS data file."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"_description": "Unsupported ATS systems", "ats_systems": {}}


def save_unsupported_ats(data: Dict):
    """Save the unsupported ATS data file."""
    data["_updated_at"] = datetime.now().isoformat()
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def discover_api_endpoints(url: str) -> Dict[str, Any]:
    """
    Analyze a careers page to discover API endpoints.
    Uses Playwright to intercept network requests.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}

    result = {
        "url": url,
        "discovered_at": datetime.now().isoformat(),
        "api_endpoints": [],
        "json_responses": [],
        "ats_hints": [],
        "error": None,
    }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            page = await context.new_page()

            # Collect API requests
            api_requests = []

            async def handle_request(request):
                url = request.url
                # Look for API-like endpoints
                if any(x in url.lower() for x in ['/api/', '/v1/', '/v2/', '/graphql', '/jobs', '/postings', '/careers']):
                    if not any(x in url for x in ['.js', '.css', '.png', '.jpg', '.svg', '.woff']):
                        api_requests.append({
                            "url": url,
                            "method": request.method,
                            "resource_type": request.resource_type,
                        })

            async def handle_response(response):
                url = response.url
                content_type = response.headers.get("content-type", "")

                # Capture JSON responses
                if "application/json" in content_type:
                    try:
                        body = await response.json()
                        # Check if it looks like job data
                        body_str = json.dumps(body)[:500]
                        if any(x in body_str.lower() for x in ["job", "position", "title", "posting", "career"]):
                            result["json_responses"].append({
                                "url": url,
                                "sample": body_str[:200],
                                "has_jobs_array": "jobs" in body_str.lower(),
                            })
                    except:
                        pass

            page.on("request", handle_request)
            page.on("response", handle_response)

            # Navigate to the page
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)  # Wait for dynamic content

            # Try scrolling to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            result["api_endpoints"] = api_requests

            # Analyze page for ATS hints
            html = await page.content()

            # Check for known ATS patterns
            ats_patterns = {
                "phenom": [r"phenom", r"talentbrew", r"phenompeople"],
                "icims": [r"icims", r"i[cC]ims"],
                "taleo": [r"taleo", r"oracle.*cloud.*careers"],
                "successfactors": [r"successfactors", r"sap.*careers"],
                "jobvite": [r"jobvite"],
                "breezy": [r"breezy\.hr", r"breezyhr"],
                "recruitee": [r"recruitee"],
                "bamboohr": [r"bamboohr"],
                "jazz": [r"jazz\.co", r"resumator"],
                "ultipro": [r"ultipro"],
            }

            html_lower = html.lower()
            for ats_name, patterns in ats_patterns.items():
                for pattern in patterns:
                    if re.search(pattern, html_lower):
                        result["ats_hints"].append(ats_name)
                        break

            result["ats_hints"] = list(set(result["ats_hints"]))

            await browser.close()

    except Exception as e:
        result["error"] = str(e)

    return result


def register_unsupported_ats(ats_name: str, discovery_result: Dict, company_url: str = None):
    """
    Register or update an unsupported ATS in the data file.
    """
    data = load_unsupported_ats()

    ats_key = ats_name.lower().replace(" ", "_")

    if ats_key not in data["ats_systems"]:
        data["ats_systems"][ats_key] = {
            "name": ats_name,
            "first_seen": datetime.now().isoformat(),
            "companies_using": [],
            "api_endpoints": [],
            "sample_responses": [],
            "notes": "",
            "parser_status": "not_started",  # not_started, in_progress, completed
        }

    ats_data = data["ats_systems"][ats_key]

    # Add company if provided
    if company_url and company_url not in ats_data["companies_using"]:
        ats_data["companies_using"].append(company_url)

    # Add discovered endpoints
    for endpoint in discovery_result.get("api_endpoints", []):
        if endpoint not in ats_data["api_endpoints"]:
            ats_data["api_endpoints"].append(endpoint)

    # Add sample responses
    for sample in discovery_result.get("json_responses", []):
        if len(ats_data["sample_responses"]) < 5:  # Keep max 5 samples
            ats_data["sample_responses"].append(sample)

    ats_data["last_updated"] = datetime.now().isoformat()

    save_unsupported_ats(data)
    return ats_data


def generate_parser_template(ats_name: str) -> str:
    """
    Generate a parser template based on collected data.
    """
    data = load_unsupported_ats()
    ats_key = ats_name.lower().replace(" ", "_")

    if ats_key not in data["ats_systems"]:
        return f"# No data found for ATS: {ats_name}\n# Run discovery first."

    ats_data = data["ats_systems"][ats_key]

    # Find potential API URL
    api_url = "https://api.example.com/jobs/{company_slug}"
    for endpoint in ats_data.get("api_endpoints", []):
        if isinstance(endpoint, dict) and endpoint.get("url"):
            url = endpoint["url"]
            if "/jobs" in url or "/postings" in url or "/api/" in url:
                api_url = url
                break

    template = f'''# parsers/{ats_key}.py
"""
{ats_name} ATS Parser
Auto-generated template - requires manual completion.

Companies using this ATS:
{chr(10).join(f"  - {c}" for c in ats_data.get("companies_using", [])[:5])}

Discovered endpoints:
{chr(10).join(f"  - {e.get('url', e) if isinstance(e, dict) else e}" for e in ats_data.get("api_endpoints", [])[:5])}
"""
import requests
from typing import List, Dict


def fetch_{ats_key}_jobs(company: str, base_url: str) -> List[Dict]:
    """
    Fetch jobs from {ats_name} ATS.

    Args:
        company: Company name (for job dict)
        base_url: Company careers URL (e.g., https://careers.company.com)

    Returns:
        List of job dicts with required fields:
        - title, url, ats_job_id, location, updated_at
    """
    # TODO: Extract company slug from base_url
    # Example: slug = base_url.rstrip("/").split("/")[-1]
    slug = base_url.rstrip("/").split("/")[-1]

    # TODO: Build API URL
    # Discovered endpoint pattern: {api_url}
    api_url = f"REPLACE_WITH_ACTUAL_API_URL"

    headers = {{
        "User-Agent": "Mozilla/5.0 (compatible; JobTracker/1.0)",
        "Accept": "application/json",
    }}

    try:
        resp = requests.get(api_url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Error fetching {ats_name} jobs: {{e}}")
        return []

    jobs = []

    # TODO: Adjust based on actual API response structure
    # Sample response: {ats_data.get("sample_responses", [{}])[0].get("sample", "N/A")[:100]}

    for job in data.get("jobs", []):  # Adjust key as needed
        jobs.append({{
            "company": company,
            "ats": "{ats_key}",
            "ats_job_id": str(job.get("id", "")),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "department": job.get("department", ""),
            "url": job.get("url", ""),
            "first_published": job.get("createdAt", ""),
            "updated_at": job.get("updatedAt", ""),
        }})

    return jobs


if __name__ == "__main__":
    # Test the parser
    test_url = "{ats_data.get('companies_using', ['https://example.com/careers'])[0]}"
    print(f"Testing: {{test_url}}")

    jobs = fetch_{ats_key}_jobs("TestCompany", test_url)
    print(f"Found {{len(jobs)}} jobs")

    for job in jobs[:3]:
        print(f"  - {{job.get('title')}} @ {{job.get('location')}}")
'''

    return template


def list_unsupported_ats():
    """List all collected unsupported ATS systems."""
    data = load_unsupported_ats()

    print("\n📊 Unsupported ATS Systems\n" + "=" * 50)

    if not data["ats_systems"]:
        print("No unsupported ATS collected yet.")
        print("Run: python tools/ats_discovery.py discover <url>")
        return

    for ats_key, ats_data in data["ats_systems"].items():
        status_emoji = {
            "not_started": "⬜",
            "in_progress": "🟡",
            "completed": "✅",
        }.get(ats_data.get("parser_status", "not_started"), "⬜")

        companies_count = len(ats_data.get("companies_using", []))
        endpoints_count = len(ats_data.get("api_endpoints", []))

        print(f"\n{status_emoji} {ats_data.get('name', ats_key)}")
        print(f"   Companies: {companies_count}")
        print(f"   Endpoints discovered: {endpoints_count}")
        print(f"   Status: {ats_data.get('parser_status', 'not_started')}")

        if ats_data.get("companies_using"):
            print(f"   Example: {ats_data['companies_using'][0][:50]}...")


async def main():
    if len(sys.argv) < 2:
        print("""
ATS Discovery Tool
==================

Usage:
  python tools/ats_discovery.py discover <careers_url>  - Analyze a page for API endpoints
  python tools/ats_discovery.py list                    - List collected unsupported ATS
  python tools/ats_discovery.py generate <ats_name>     - Generate parser template
  python tools/ats_discovery.py add <ats_name> <url>    - Manually add an ATS

Examples:
  python tools/ats_discovery.py discover "https://careers.netflix.com"
  python tools/ats_discovery.py generate phenom
        """)
        return

    command = sys.argv[1]

    if command == "discover" and len(sys.argv) > 2:
        url = sys.argv[2]
        print(f"\n🔍 Analyzing: {url}\n")

        result = await discover_api_endpoints(url)

        if result.get("error"):
            print(f"❌ Error: {result['error']}")
            return

        print(f"📡 Found {len(result['api_endpoints'])} API-like requests")
        for ep in result["api_endpoints"][:10]:
            print(f"   {ep.get('method', 'GET')} {ep.get('url', '')[:80]}...")

        print(f"\n📦 Found {len(result['json_responses'])} JSON responses with job data")
        for resp in result["json_responses"][:5]:
            print(f"   {resp.get('url', '')[:60]}...")
            print(f"      Sample: {resp.get('sample', '')[:80]}...")

        if result["ats_hints"]:
            print(f"\n🏷️  ATS detected: {', '.join(result['ats_hints'])}")

            # Register the ATS
            for ats_name in result["ats_hints"]:
                register_unsupported_ats(ats_name, result, url)
                print(f"   → Registered {ats_name} in unsupported_ats.json")
        else:
            print("\n⚠️  Could not identify ATS. You can manually add it:")
            print(f"   python tools/ats_discovery.py add <ats_name> {url}")

    elif command == "list":
        list_unsupported_ats()

    elif command == "generate" and len(sys.argv) > 2:
        ats_name = sys.argv[2]
        auto_save = "--save" in sys.argv
        template = generate_parser_template(ats_name)

        # Save to file
        output_path = PARSERS_DIR / f"{ats_name.lower().replace(' ', '_')}.py"

        print(f"\n📝 Generated parser template for: {ats_name}")
        print(f"   Output: {output_path}\n")
        print("-" * 50)
        print(template[:1000] + "..." if len(template) > 1000 else template)
        print("-" * 50)

        should_save = auto_save
        if not auto_save:
            try:
                response = input(f"\nSave to {output_path}? [y/N]: ")
                should_save = response.lower() == "y"
            except EOFError:
                # Non-interactive mode, don't save unless --save flag
                print("\n(Non-interactive mode. Use --save flag to auto-save)")
                should_save = False

        if should_save:
            with open(output_path, "w") as f:
                f.write(template)
            print(f"✅ Saved to {output_path}")
            print("\nNext steps:")
            print("1. Edit the parser to match the actual API")
            print("2. Add to ATS_PARSERS in main.py")
            print(f"3. Test with: python parsers/{ats_name}.py")

    elif command == "add" and len(sys.argv) > 3:
        ats_name = sys.argv[2]
        url = sys.argv[3]

        register_unsupported_ats(ats_name, {"api_endpoints": [], "json_responses": []}, url)
        print(f"✅ Added {ats_name} with URL: {url}")
        print(f"   Data saved to: {DATA_FILE}")

    else:
        print("Invalid command. Run without arguments for help.")


if __name__ == "__main__":
    asyncio.run(main())
