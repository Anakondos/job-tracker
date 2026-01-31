"""
Browser-based job page parser using Playwright.
Renders JavaScript pages and extracts job description text.
Can detect "No longer hiring" and similar messages.
"""

import asyncio
import base64
import re
from typing import Optional, Dict, Any


async def parse_job_page_with_browser(url: str, take_screenshot: bool = False) -> Dict[str, Any]:
    """
    Parse a job page using headless browser (Playwright).
    Renders JavaScript and extracts text content.
    
    Returns:
        {
            "ok": True/False,
            "title": str,
            "company": str,
            "location": str,
            "salary": str,
            "jd": str (job description text),
            "is_closed": bool,
            "closed_reason": str,
            "screenshot_base64": str (if take_screenshot=True),
            "error": str (if failed)
        }
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "error": "Playwright not installed. Run: pip3 install playwright && playwright install chromium"}
    
    result = {
        "ok": False,
        "title": "",
        "company": "",
        "location": "",
        "salary": "",
        "jd": "",
        "is_closed": False,
        "closed_reason": "",
        "screenshot_base64": None
    }
    
    # Patterns that indicate job is closed/expired
    closed_patterns = [
        r"no longer hiring",
        r"position closed",
        r"job.*closed",
        r"position.*filled",
        r"no longer accepting",
        r"this job is no longer available",
        r"this position has been filled",
        r"job.*expired",
        r"listing.*expired",
        r"application.*closed",
        r"sorry.*position.*no longer",
        r"this role is no longer open",
        r"job posting has been removed",
    ]
    
    try:
        async with async_playwright() as p:
            # Launch headless browser
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Navigate and wait for content
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Wait a bit more for dynamic content
            await asyncio.sleep(2)
            
            # Take screenshot if requested or if job might be closed
            page_text = await page.inner_text("body")
            page_text_lower = page_text.lower()
            
            # Check if job is closed
            for pattern in closed_patterns:
                if re.search(pattern, page_text_lower):
                    result["is_closed"] = True
                    result["closed_reason"] = pattern.replace(r".*", " ").replace(r"\.", "").strip()
                    take_screenshot = True  # Always screenshot closed jobs
                    break
            
            if take_screenshot:
                screenshot_bytes = await page.screenshot(type="jpeg", quality=80)
                result["screenshot_base64"] = base64.b64encode(screenshot_bytes).decode()
            
            # Try to extract structured data
            # Title - try common selectors
            title_selectors = [
                "h1",
                "[data-testid='job-title']",
                ".job-title",
                ".position-title",
                "[class*='JobTitle']",
                "[class*='job-title']",
            ]
            for sel in title_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if text and len(text) > 3 and len(text) < 200:
                            result["title"] = text.strip()
                            break
                except:
                    continue
            
            # Company - try common selectors
            company_selectors = [
                "[data-testid='company-name']",
                ".company-name",
                ".employer-name",
                "[class*='CompanyName']",
                "[class*='company-name']",
                "a[href*='/company/']",
                "a[href*='/employer/']",
                # RemoteHunter specific - company name under title
                "h1 + div",
                "h1 ~ p",
            ]
            for sel in company_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if text and len(text) > 1 and len(text) < 100:
                            result["company"] = text.strip()
                            break
                except:
                    continue
            
            # Location - try common selectors
            location_selectors = [
                "[data-testid='job-location']",
                ".job-location",
                ".location",
                "[class*='Location']",
                "[class*='location']",
            ]
            for sel in location_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if text and len(text) > 2 and len(text) < 200:
                            result["location"] = text.strip()
                            break
                except:
                    continue
            
            # Salary - look for salary patterns in text
            salary_match = re.search(r'\$[\d,]+(?:\s*[-–]\s*\$?[\d,]+)?(?:\s*(?:per\s+)?(?:year|yr|annually|/yr|/year))?', page_text, re.IGNORECASE)
            if salary_match:
                result["salary"] = salary_match.group(0)
            
            # Job Description - get main content
            # Try to find job description section
            jd_selectors = [
                "[data-testid='job-description']",
                ".job-description",
                ".description",
                "[class*='JobDescription']",
                "[class*='job-description']",
                "article",
                "main",
                "#job-details",
                ".job-details",
            ]
            
            jd_text = ""
            for sel in jd_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if text and len(text) > len(jd_text):
                            jd_text = text
                except:
                    continue
            
            # If no JD section found, use body text
            if len(jd_text) < 200:
                jd_text = page_text
            
            # Clean up the text
            jd_text = clean_job_text(jd_text)
            result["jd"] = jd_text
            
            # If we didn't find title/company from selectors, try to extract from text
            if not result["title"] and result["jd"]:
                lines = result["jd"].split("\n")
                for line in lines[:10]:
                    line = line.strip()
                    if 10 < len(line) < 100 and not any(x in line.lower() for x in ["cookie", "privacy", "log in", "sign up"]):
                        result["title"] = line
                        break
            
            # Try to extract company from page text patterns
            if not result["company"] and page_text:
                # Common patterns: "at CompanyName", "Company: X", logo alt text
                company_patterns = [
                    r'(?:^|\n)([A-Z][A-Za-z0-9\s&]+?)(?:\n|$)(?=.*(?:week|day|month|ago|posted))',  # Company name before date
                    r'(?:at|@)\s+([A-Z][A-Za-z0-9\s&]{2,30}?)(?:\n|,|\.)',
                    r'Company:\s*([A-Za-z0-9\s&]{2,30})',
                ]
                for pattern in company_patterns:
                    match = re.search(pattern, page_text[:2000])
                    if match:
                        company_candidate = match.group(1).strip()
                        # Filter out common non-company text
                        if company_candidate and len(company_candidate) > 2 and len(company_candidate) < 50:
                            if not any(x in company_candidate.lower() for x in ["remote", "job", "position", "apply", "description", "salary"]):
                                result["company"] = company_candidate
                                break
            
            await browser.close()
            
            result["ok"] = len(result["jd"]) > 100
            return result
            
    except Exception as e:
        result["error"] = str(e)
        return result


def clean_job_text(text: str) -> str:
    """Clean and normalize job description text."""
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    
    # Remove common non-job content
    remove_patterns = [
        r'Accept.*cookies?.*',
        r'Cookie.*policy.*',
        r'Privacy.*policy.*',
        r'Terms.*service.*',
        r'Log ?in.*',
        r'Sign ?up.*',
        r'Create.*account.*',
        r'Subscribe.*newsletter.*',
        r'Follow us on.*',
        r'Share this job.*',
        r'Report this job.*',
        r'Similar jobs.*',
        r'You may also like.*',
        r'©.*\d{4}.*',
    ]
    
    for pattern in remove_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Trim to reasonable length
    if len(text) > 8000:
        text = text[:8000]
    
    return text.strip()


def parse_job_page_sync(url: str, take_screenshot: bool = False) -> Dict[str, Any]:
    """Synchronous wrapper for parse_job_page_with_browser."""
    import nest_asyncio
    try:
        # Allow nested event loops (needed when called from FastAPI)
        nest_asyncio.apply()
    except:
        pass
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Create a new thread to run the async function
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, parse_job_page_with_browser(url, take_screenshot))
                return future.result(timeout=60)
        else:
            return asyncio.run(parse_job_page_with_browser(url, take_screenshot))
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Test
if __name__ == "__main__":
    test_url = "https://www.remotehunter.com/apply-with-ai/22b9f956-4adf-42af-bc33-43b7beada28f"
    result = parse_job_page_sync(test_url, take_screenshot=True)
    print(f"OK: {result['ok']}")
    print(f"Title: {result['title']}")
    print(f"Company: {result['company']}")
    print(f"Is Closed: {result['is_closed']}")
    print(f"JD length: {len(result['jd'])}")
    print(f"Screenshot: {'Yes' if result['screenshot_base64'] else 'No'}")
    if result.get("error"):
        print(f"Error: {result['error']}")
