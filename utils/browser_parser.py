"""
Browser-based job page parser using Playwright.
Renders JavaScript pages and extracts job description text.
Can detect "No longer hiring" and similar messages.
Can also detect intermediate/aggregator pages that redirect to actual application.
"""

import asyncio
import base64
import re
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse


# Known job aggregator domains - these typically redirect to the actual company career page
AGGREGATOR_DOMAINS = [
    "dejobs.org",
    "indeed.com",
    "linkedin.com/jobs",
    "glassdoor.com",
    "ziprecruiter.com",
    "monster.com",
    "careerbuilder.com",
    "simplyhired.com",
    "remotehunter.com",
    "wellfound.com",  # AngelList
    "builtin.com",
    "dice.com",
    "flexjobs.com",
    "weworkremotely.com",
    "remoteok.com",
]


def is_aggregator_url(url: str) -> bool:
    """Check if URL is from a known job aggregator."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(agg in domain for agg in AGGREGATOR_DOMAINS)


async def check_apply_button_destination(page, url: str) -> Dict[str, Any]:
    """
    Check where the Apply button leads to.
    Returns info about whether this is an intermediate page or direct application.

    Returns:
        {
            "is_intermediate": bool,  # True if Apply leads to another job page
            "has_apply_form": bool,   # True if current page has application form
            "apply_url": str,         # URL the Apply button leads to (if external)
            "apply_button_text": str, # Text of the Apply button
            "form_fields": list,      # List of form field types found
        }
    """
    result = {
        "is_intermediate": False,
        "has_apply_form": False,
        "apply_url": None,
        "apply_button_text": "",
        "form_fields": [],
    }

    try:
        # 1. Check if current page already has application form fields
        form_field_selectors = [
            "input[name*='name']",
            "input[name*='email']",
            "input[name*='phone']",
            "input[type='file']",  # Resume upload
            "textarea[name*='cover']",
            "input[name*='resume']",
            "[data-testid*='resume']",
            "[data-testid*='upload']",
            # Aria-label based (for Phenom ATS like Cisco)
            "input[aria-label*='Name']",
            "input[aria-label*='Email']",
            "input[aria-label*='Phone']",
            "input[aria-label*='Address']",
            "input[aria-label*='First']",
            "input[aria-label*='Last']",
        ]

        for selector in form_field_selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    result["form_fields"].append(selector)
            except:
                continue

        # Also count general form inputs as indicator
        try:
            all_inputs = await page.query_selector_all("input[type='text'], input[type='email'], input[type='tel']")
            if len(all_inputs) >= 5:
                # Page has many input fields - likely an application form
                result["form_fields"].append(f"general_inputs:{len(all_inputs)}")
        except:
            pass

        if len(result["form_fields"]) >= 2:
            result["has_apply_form"] = True
            return result

        # 2. Find Apply button and check its destination
        apply_button_selectors = [
            "a[href*='apply']",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "[data-testid*='apply']",
            ".apply-button",
            "#apply-button",
            "a[class*='apply']",
            "button[class*='apply']",
        ]

        for selector in apply_button_selectors:
            try:
                buttons = await page.query_selector_all(selector)
                for btn in buttons:
                    text = await btn.inner_text()
                    text = text.strip().lower()

                    # Skip if it's just "Applied" or similar status
                    if text in ["applied", "saved", "share"]:
                        continue

                    # Check if it's a link with href
                    href = await btn.get_attribute("href")
                    if href:
                        # Normalize href
                        if href.startswith("/"):
                            parsed = urlparse(url)
                            href = f"{parsed.scheme}://{parsed.netloc}{href}"

                        result["apply_button_text"] = text

                        # Check if it leads to external domain
                        current_domain = urlparse(url).netloc.lower()
                        target_domain = urlparse(href).netloc.lower() if href.startswith("http") else current_domain

                        if target_domain != current_domain:
                            result["is_intermediate"] = True
                            result["apply_url"] = href
                            return result

                    # If button has no href, might open modal - that's direct application
                    if not href:
                        result["apply_button_text"] = text
                        # Assume it's direct application if no external link
                        break

            except:
                continue

        # 3. Check for known aggregator patterns in page
        page_text = await page.inner_text("body")
        page_text_lower = page_text.lower()

        aggregator_patterns = [
            r"apply\s+on\s+company\s+(?:site|website)",
            r"apply\s+(?:at|on)\s+[a-z]+\.com",
            r"view\s+original\s+(?:posting|job)",
            r"apply\s+externally",
            r"external\s+application",
            r"continue\s+to\s+application",
        ]

        for pattern in aggregator_patterns:
            if re.search(pattern, page_text_lower):
                result["is_intermediate"] = True
                break

    except Exception as e:
        result["error"] = str(e)

    return result


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
        "screenshot_base64": None,
        # Application flow info
        "is_intermediate": False,  # True if this is an aggregator page
        "has_apply_form": False,   # True if page has direct application form
        "apply_url": None,         # URL of actual application page (if intermediate)
        "is_aggregator": is_aggregator_url(url),
    }
    
    # Patterns that indicate job is closed/expired
    # NOTE: Be careful with patterns - avoid matching conditional phrases like "if position is filled"
    closed_patterns = [
        r"no longer hiring",
        r"position closed",
        r"job.*closed",
        r"(?<!if\s)(?<!if\sthe\s)position\s+(has\s+been\s+|is\s+)filled",  # Match "position has been filled" but not "if the position is filled"
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

            # Check apply button destination (for intermediate page detection)
            apply_info = await check_apply_button_destination(page, url)
            result["is_intermediate"] = apply_info.get("is_intermediate", False)
            result["has_apply_form"] = apply_info.get("has_apply_form", False)
            result["apply_url"] = apply_info.get("apply_url")

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


async def navigate_to_application_form(url: str, max_redirects: int = 3) -> Dict[str, Any]:
    """
    Navigate from a job posting URL to the actual application form.
    Handles intermediate pages, aggregators, and Apply button clicks.

    Returns:
        {
            "ok": bool,
            "final_url": str,           # URL where application form was found
            "original_url": str,        # Starting URL
            "redirects": list,          # List of URLs navigated through
            "has_form": bool,           # True if form fields were found
            "form_fields_count": int,   # Number of form fields detected
            "needs_apply_click": bool,  # True if Apply button needs to be clicked
            "error": str,
        }
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"ok": False, "error": "Playwright not installed"}

    result = {
        "ok": False,
        "final_url": url,
        "original_url": url,
        "redirects": [],
        "has_form": False,
        "form_fields_count": 0,
        "needs_apply_click": False,
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

            current_url = url
            redirects = []

            for _ in range(max_redirects):
                await page.goto(current_url, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)

                # Check current page
                apply_info = await check_apply_button_destination(page, current_url)

                # If this is an intermediate page with external apply URL
                if apply_info.get("is_intermediate") and apply_info.get("apply_url"):
                    redirects.append(current_url)
                    current_url = apply_info["apply_url"]
                    continue

                # If page has application form
                if apply_info.get("has_apply_form"):
                    result["has_form"] = True
                    result["form_fields_count"] = len(apply_info.get("form_fields", []))
                    break

                # If we're on a direct page but no form yet - might need to click Apply
                # Try to find and click Apply button
                apply_selectors = [
                    "button:has-text('Apply')",
                    "a:has-text('Apply Now')",
                    "a:has-text('Apply')",
                    "[data-testid*='apply']",
                    ".apply-button",
                    "#apply-button",
                ]

                clicked = False
                for selector in apply_selectors:
                    try:
                        btn = await page.query_selector(selector)
                        if btn:
                            # Check if it's a link that stays on same domain
                            href = await btn.get_attribute("href")
                            if href and href.startswith("http"):
                                target_domain = urlparse(href).netloc
                                current_domain = urlparse(current_url).netloc
                                if target_domain != current_domain:
                                    # External link - follow it
                                    redirects.append(current_url)
                                    current_url = href
                                    clicked = True
                                    break

                            # Click the button (might open modal or navigate)
                            await btn.click()
                            await asyncio.sleep(2)
                            result["needs_apply_click"] = True
                            clicked = True

                            # Check if form appeared after click
                            apply_info = await check_apply_button_destination(page, page.url)
                            if apply_info.get("has_apply_form"):
                                result["has_form"] = True
                                result["form_fields_count"] = len(apply_info.get("form_fields", []))

                            break
                    except:
                        continue

                if not clicked:
                    # No more navigation possible
                    break

            result["ok"] = True
            result["final_url"] = page.url
            result["redirects"] = redirects

            await browser.close()

    except Exception as e:
        result["error"] = str(e)

    return result


def navigate_to_application_form_sync(url: str, max_redirects: int = 3) -> Dict[str, Any]:
    """Synchronous wrapper for navigate_to_application_form."""
    import nest_asyncio
    try:
        nest_asyncio.apply()
    except:
        pass

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, navigate_to_application_form(url, max_redirects))
                return future.result(timeout=120)
        else:
            return asyncio.run(navigate_to_application_form(url, max_redirects))
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
