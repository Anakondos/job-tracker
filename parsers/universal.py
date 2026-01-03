"""
Universal job parser using Playwright for any job page.
Extracts job details from rendered HTML using common patterns.
"""

from playwright.sync_api import sync_playwright
from datetime import datetime
import re
import json


def extract_job_details(url: str, timeout: int = 15000) -> dict:
    """
    Parse any job page using Playwright headless browser.
    Returns normalized job data.
    """
    result = {
        "url": url,
        "title": None,
        "company": None,
        "location": None,
        "description": None,
        "salary": None,
        "job_type": None,
        "parsed_at": datetime.utcnow().isoformat(),
        "source": "universal_playwright"
    }
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            page.goto(url, timeout=timeout, wait_until="networkidle")
            page.wait_for_timeout(2000)  # Extra wait for JS rendering
            
            # Try to extract title
            result["title"] = extract_title(page)
            
            # Try to extract company
            result["company"] = extract_company(page, url)
            
            # Try to extract location
            result["location"] = extract_location(page)
            
            # Try to extract description
            result["description"] = extract_description(page)
            
            # Try to extract salary
            result["salary"] = extract_salary(page)
            
            # Try to extract job type
            result["job_type"] = extract_job_type(page)
            
        except Exception as e:
            result["error"] = str(e)
        finally:
            browser.close()
    
    return result


def extract_title(page) -> str:
    """Extract job title using common selectors and patterns."""
    # Try og:title first (often has clean job title)
    try:
        og_title = page.query_selector("meta[property=\"og:title\"]")
        if og_title:
            content = og_title.get_attribute("content")
            if content and len(content) > 10:
                # Clean common prefixes like "Check out this job at Company, "
                if ", " in content and "job at" in content.lower():
                    return content.split(", ", 1)[-1].strip()
                return content
    except:
        pass
    
    selectors = [
        # Common job title selectors
        'h1[class*="title"]',
        'h1[class*="job"]',
        'h1[data-testid*="title"]',
        '[class*="job-title"]',
        '[class*="jobTitle"]',
        '[class*="position-title"]',
        '[data-automation="job-title"]',
        '.posting-headline h2',
        '.job-header h1',
        '.job-title',
        'h1.title',
        # Generic h1 as fallback
        'h1',
    ]
    
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text and len(text) > 3 and len(text) < 200:
                    return text
        except:
            continue
    
    # Try from page title
    try:
        title = page.title()
        if title:
            # Clean common suffixes
            for suffix in [" - ", " | ", " at ", " – "]:
                if suffix in title:
                    return title.split(suffix)[0].strip()
            return title
    except:
        pass
    
    return None


def extract_company(page, url: str) -> str:
    """Extract company name using selectors and URL patterns."""
    selectors = [
        '[class*="company-name"]',
        '[class*="companyName"]',
        '[class*="employer"]',
        '[data-testid*="company"]',
        '[class*="organization"]',
        '.company-name',
        '.employer-name',
        'a[href*="/company/"]',
    ]
    
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text and len(text) > 1 and len(text) < 100:
                    return text
        except:
            continue
    
    # Try to extract from URL
    try:
        # Common patterns: jobs.company.com, company.workday.com, boards.greenhouse.io/company
        if "greenhouse.io/" in url:
            match = re.search(r'greenhouse\.io/([^/]+)', url)
            if match:
                return match.group(1).replace('-', ' ').title()
        elif "lever.co/" in url:
            match = re.search(r'lever\.co/([^/]+)', url)
            if match:
                return match.group(1).replace('-', ' ').title()
        elif "myworkdayjobs.com" in url:
            match = re.search(r'([^.]+)\.myworkdayjobs\.com', url)
            if match:
                return match.group(1).replace('-', ' ').title()
        elif "jobs." in url:
            match = re.search(r'jobs\.([^.]+)\.', url)
            if match:
                return match.group(1).replace('-', ' ').title()
    except:
        pass
    
    return None


def extract_location(page) -> str:
    """Extract job location."""
    selectors = [
        '[class*="location"]',
        '[class*="Location"]',
        '[data-testid*="location"]',
        '[class*="job-location"]',
        '.location',
        '.job-location',
        '[class*="workplace"]',
    ]
    
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text and len(text) > 2 and len(text) < 200:
                    # Clean up common prefixes
                    text = re.sub(r'^(Location:?|📍|🌍)\s*', '', text, flags=re.IGNORECASE)
                    return text.strip()
        except:
            continue
    
    # Look for Remote keywords
    try:
        body_text = page.inner_text('body')
        if 'Remote' in body_text[:2000]:
            remote_match = re.search(r'(Remote|Fully Remote|100% Remote|Work from Home)', body_text[:2000], re.IGNORECASE)
            if remote_match:
                return remote_match.group(0)
    except:
        pass
    
    return None


def extract_description(page) -> str:
    """Extract job description text."""
    selectors = [
        '[class*="description"]',
        '[class*="job-description"]',
        '[class*="jobDescription"]',
        '[data-testid*="description"]',
        '.job-description',
        '.description',
        'article',
        '[class*="content"]',
    ]
    
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text and len(text) > 100:
                    # Truncate to reasonable length
                    return text[:5000]
        except:
            continue
    
    # Fallback: get body text and extract job description section
    try:
        body = page.inner_text('body')
        # Look for Job Description section
        if 'Job Description' in body:
            start = body.find('Job Description')
            # Find end markers
            end_markers = ['Apply now', 'Apply Now', 'Submit Application', 'First name', 'Similar Jobs']
            end = len(body)
            for marker in end_markers:
                pos = body.find(marker, start)
                if pos > start and pos < end:
                    end = pos
            desc = body[start:end].strip()
            if len(desc) > 100:
                return desc[:5000]
    except:
        pass
    
    return None


def extract_salary(page) -> str:
    """Extract salary information if available."""
    selectors = [
        '[class*="salary"]',
        '[class*="compensation"]',
        '[class*="pay"]',
        '[data-testid*="salary"]',
    ]
    
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text and ('$' in text or 'k' in text.lower() or 'salary' in text.lower()):
                    return text[:200]
        except:
            continue
    
    # Try regex on page content
    try:
        body = page.inner_text('body')[:3000]
        salary_match = re.search(r'\$[\d,]+(?:\s*-\s*\$[\d,]+)?(?:\s*(?:per|\/)\s*(?:year|hour|yr|hr))?', body, re.IGNORECASE)
        if salary_match:
            return salary_match.group(0)
    except:
        pass
    
    return None


def extract_job_type(page) -> str:
    """Extract job type (Full-time, Part-time, Contract, etc.)."""
    selectors = [
        '[class*="job-type"]',
        '[class*="employment-type"]',
        '[class*="workType"]',
    ]
    
    for selector in selectors:
        try:
            el = page.query_selector(selector)
            if el:
                text = el.inner_text().strip()
                if text and len(text) < 50:
                    return text
        except:
            continue
    
    # Try regex
    try:
        body = page.inner_text('body')[:2000]
        type_match = re.search(r'(Full[- ]?time|Part[- ]?time|Contract|Freelance|Temporary|Internship)', body, re.IGNORECASE)
        if type_match:
            return type_match.group(0)
    except:
        pass
    
    return None


# Test
if __name__ == "__main__":
    test_url = "https://jobs.micro1.ai/post/a992e7aa-9629-4add-9e10-b97bca4ea62f"
    print(f"Testing URL: {test_url}")
    result = extract_job_details(test_url)
    print(json.dumps(result, indent=2))
