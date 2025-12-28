"""
Micro1.ai jobs parser using Playwright.
"""

from playwright.sync_api import sync_playwright
from datetime import datetime, timezone
import re


def parse_micro1_jobs(board_url: str = "https://jobs.micro1.ai/") -> list:
    """
    Parse all jobs from micro1.ai jobs page.
    Returns list of job dicts.
    """
    jobs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = context.new_page()
        
        try:
            page.goto(board_url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(3000)
            
            # Scroll to load more jobs if infinite scroll
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
            
            # Find all job links
            links = page.query_selector_all('a[href*="/post/"]')
            
            seen_urls = set()
            for link in links:
                try:
                    href = link.get_attribute('href')
                    if not href or href in seen_urls:
                        continue
                    
                    # Clean URL (remove utm params)
                    clean_url = href.split('?')[0]
                    if clean_url in seen_urls:
                        continue
                    seen_urls.add(clean_url)
                    
                    # Make absolute URL
                    if not clean_url.startswith('http'):
                        clean_url = f"https://jobs.micro1.ai{clean_url}"
                    
                    # Get text content
                    text = link.inner_text().strip()
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    
                    # Parse job info from card
                    title = ""
                    location = "Remote"
                    date_str = ""
                    
                    for line in lines:
                        # Date line: "Dec 27, 2025"
                        if re.match(r'^[A-Z][a-z]{2} \d{1,2}, \d{4}$', line):
                            date_str = line
                        # Skip company name "micro1"
                        elif line.lower() == "micro1":
                            continue
                        # Location
                        elif line.lower() in ["remote", "on-site", "hybrid"]:
                            location = line
                        # Title (usually the longest meaningful line)
                        elif len(line) > 5 and not title:
                            title = line
                    
                    if not title:
                        continue
                    
                    # Extract job ID from URL
                    job_id_match = re.search(r'/post/([a-f0-9-]+)', clean_url)
                    job_id = job_id_match.group(1) if job_id_match else ""
                    
                    job = {
                        "title": title,
                        "company": "micro1",
                        "location": location,
                        "job_url": clean_url,
                        "ats_job_id": job_id,
                        "ats": "micro1",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    jobs.append(job)
                    
                except Exception as e:
                    print(f"Error parsing job card: {e}")
                    continue
            
        except Exception as e:
            print(f"Error loading micro1 jobs: {e}")
        finally:
            browser.close()
    
    return jobs


def fetch_micro1_job_details(url: str) -> dict:
    """
    Fetch detailed info for a single micro1 job.
    """
    from parsers.universal import extract_job_details
    return extract_job_details(url)


# Test
if __name__ == "__main__":
    print("Parsing micro1.ai jobs...")
    jobs = parse_micro1_jobs()
    print(f"Found {len(jobs)} jobs:")
    for j in jobs[:10]:
        print(f"  - {j['title']} ({j['location']})")
