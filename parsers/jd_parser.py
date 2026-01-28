"""
Job Description Parser & Analyzer
Parses JD from job page and extracts structured summary using Claude API
"""

import os
import json
import re
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
JD_DIR = DATA_DIR / "jd"
JD_DIR.mkdir(exist_ok=True)


def get_api_key() -> Optional[str]:
    """Get Anthropic API key from config"""
    config_paths = [
        Path(__file__).parent.parent / "browser" / "v5" / "config" / "api_keys.json",
        Path(__file__).parent.parent / "config" / "api_keys.json",
    ]
    for config_path in config_paths:
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text())
                return config.get("anthropic_api_key") or config.get("ANTHROPIC_API_KEY")
            except:
                pass
    return os.environ.get("ANTHROPIC_API_KEY")


def fetch_jd_from_url(url: str, ats: str = "greenhouse") -> Optional[str]:
    """Fetch job description text from URL"""
    try:
        # For Greenhouse, use the API instead of scraping
        if ats == "greenhouse" or "greenhouse" in url:
            jd = _fetch_greenhouse_api(url)
            if jd:
                return jd
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
        
        # Extract JD based on ATS
        if ats == "greenhouse" or "greenhouse" in url:
            return _parse_greenhouse_jd(html)
        elif ats == "lever" or "lever" in url:
            return _parse_lever_jd(html)
        elif ats == "workday" or "myworkday" in url:
            return _parse_workday_jd(html)
        else:
            # Generic extraction
            return _parse_generic_jd(html)
            
    except Exception as e:
        print(f"[JD Parser] Error fetching {url}: {e}")
        return None


def _fetch_greenhouse_api(url: str) -> Optional[str]:
    """Fetch JD from Greenhouse API - much more reliable than scraping"""
    import re
    
    # Extract job ID and company from URL
    # URLs like: https://boards.greenhouse.io/company/jobs/123
    # Or: https://company.com/careers/jobs/123?gh_jid=123
    
    job_id = None
    company = None
    
    # Try to extract gh_jid from URL
    gh_jid_match = re.search(r'gh_jid=(\d+)', url)
    if gh_jid_match:
        job_id = gh_jid_match.group(1)
    
    # Try job ID from path
    if not job_id:
        job_id_match = re.search(r'/jobs?/(\d+)', url)
        if job_id_match:
            job_id = job_id_match.group(1)
    
    # Try to find company from boards.greenhouse.io URL
    boards_match = re.search(r'boards\.greenhouse\.io/([^/]+)', url)
    if boards_match:
        company = boards_match.group(1)
    
    # Try common company career page patterns
    if not company:
        # abnormal.ai -> abnormalsecurity
        # coinbase.com -> coinbase
        domain_match = re.search(r'https?://(?:www\.)?(?:careers\.)?([^./]+)', url)
        if domain_match:
            domain = domain_match.group(1).lower()
            # Map common domains to Greenhouse board names
            domain_map = {
                'abnormal': 'abnormalsecurity',
                'coinbase': 'coinbase',
                'pagerduty': 'pagerduty',
                'stripe': 'stripe',
                'twilio': 'twilio',
            }
            company = domain_map.get(domain, domain)
    
    if not job_id or not company:
        print(f"[JD Parser] Could not extract job_id or company from URL: {url}")
        return None
    
    # Call Greenhouse API
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"
    print(f"[JD Parser] Fetching from Greenhouse API: {api_url}")
    
    try:
        resp = requests.get(api_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        content = data.get("content", "")
        if content:
            # Content is HTML-encoded, decode it
            content = content.replace("&lt;", "<").replace("&gt;", ">")
            content = content.replace("&quot;", '"').replace("&amp;", "&")
            content = content.replace("&nbsp;", " ")
            # Clean HTML tags
            return _clean_html(content)
        
    except Exception as e:
        print(f"[JD Parser] Greenhouse API error: {e}")
    
    return None


def _parse_greenhouse_jd(html: str) -> Optional[str]:
    """Extract JD from Greenhouse page"""
    import re
    
    # Try to find job description content
    patterns = [
        r'<div[^>]*id="content"[^>]*>(.*?)</div>\s*<div[^>]*id="application"',
        r'<div[^>]*class="[^"]*job-description[^"]*"[^>]*>(.*?)</div>',
        r'<section[^>]*class="[^"]*job[^"]*"[^>]*>(.*?)</section>',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _clean_html(match.group(1))
    
    # Fallback: extract all text from body
    body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.IGNORECASE)
    if body_match:
        return _clean_html(body_match.group(1))[:10000]
    
    return None


def _parse_lever_jd(html: str) -> Optional[str]:
    """Extract JD from Lever page"""
    import re
    
    patterns = [
        r'<div[^>]*class="[^"]*posting-page[^"]*"[^>]*>(.*?)<div[^>]*class="[^"]*application[^"]*"',
        r'<div[^>]*data-qa="[^"]*description[^"]*"[^>]*>(.*?)</div>',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            return _clean_html(match.group(1))
    
    return _parse_generic_jd(html)


def _parse_workday_jd(html: str) -> Optional[str]:
    """Extract JD from Workday page"""
    import re
    
    # Workday often has JD in JSON
    json_match = re.search(r'"jobDescription"\s*:\s*"([^"]+)"', html)
    if json_match:
        jd = json_match.group(1)
        jd = jd.encode().decode('unicode_escape')
        return _clean_html(jd)
    
    return _parse_generic_jd(html)


def _parse_generic_jd(html: str) -> Optional[str]:
    """Generic JD extraction"""
    # Remove script and style
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Try common patterns
    patterns = [
        r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            text = _clean_html(match.group(1))
            if len(text) > 200:
                return text
    
    # Last resort: clean whole body
    return _clean_html(html)[:10000]


def _clean_html(html: str) -> str:
    """Remove HTML tags and clean text"""
    # Remove tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'")
    # Clean whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def analyze_jd_with_ai(jd_text: str, job_title: str, company: str) -> Optional[Dict[str, Any]]:
    """Send JD to Claude for structured analysis"""
    api_key = get_api_key()
    if not api_key:
        print("[JD Parser] No API key found")
        return None
    
    if not jd_text or len(jd_text) < 100:
        print("[JD Parser] JD text too short")
        return None
    
    prompt = f"""Analyze this job description and extract structured information.

Job Title: {job_title}
Company: {company}

Job Description:
{jd_text[:8000]}

Return a JSON object with these fields:
{{
  "keywords": ["list of 5-10 key skills/technologies mentioned"],
  "requirements": ["list of 3-7 must-have requirements"],
  "nice_to_have": ["list of 2-5 nice-to-have qualifications"],
  "responsibilities": ["list of 3-5 main responsibilities"],
  "tech_stack": ["specific technologies, tools, platforms mentioned"],
  "years_experience": "extracted years requirement or null",
  "role_level": "junior/mid/senior/lead/director/vp",
  "domain": "industry domain (fintech, security, healthcare, etc)",
  "remote_friendly": true/false/null,
  "summary": "2-3 sentence summary of the role"
}}

Return ONLY valid JSON, no other text."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        resp.raise_for_status()
        
        content = resp.json()["content"][0]["text"]
        
        # Extract JSON from response
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
        
    except Exception as e:
        print(f"[JD Parser] AI analysis error: {e}")
    
    return None


def parse_and_store_jd(job_id: str, url: str, title: str, company: str, ats: str = "greenhouse") -> Dict[str, Any]:
    """
    Main function: fetch JD, analyze with AI, store both full text and summary
    
    Returns: {"ok": True, "summary": {...}} or {"ok": False, "error": "..."}
    """
    print(f"[JD Parser] Processing: {company} - {title}")
    
    # 1. Fetch JD from URL
    jd_text = fetch_jd_from_url(url, ats)
    if not jd_text:
        return {"ok": False, "error": "Failed to fetch job description"}
    
    print(f"[JD Parser] Fetched {len(jd_text)} chars")
    
    # 2. Save full text to file
    jd_file = JD_DIR / f"{job_id}.txt"
    jd_file.write_text(jd_text, encoding='utf-8')
    print(f"[JD Parser] Saved to {jd_file.name}")
    
    # 3. Analyze with AI
    summary = analyze_jd_with_ai(jd_text, title, company)
    if not summary:
        # Return basic info even if AI fails
        summary = {
            "keywords": [],
            "requirements": [],
            "nice_to_have": [],
            "responsibilities": [],
            "tech_stack": [],
            "summary": f"Job description for {title} at {company}",
            "ai_analyzed": False
        }
    else:
        summary["ai_analyzed"] = True
    
    # Add metadata
    summary["parsed_at"] = datetime.now().isoformat()
    summary["jd_length"] = len(jd_text)
    summary["jd_file"] = str(jd_file.name)
    
    print(f"[JD Parser] Analysis complete: {len(summary.get('keywords', []))} keywords")
    
    return {"ok": True, "summary": summary, "jd_text": jd_text}


def get_stored_jd(job_id: str) -> Optional[str]:
    """Get full JD text from file"""
    jd_file = JD_DIR / f"{job_id}.txt"
    if jd_file.exists():
        return jd_file.read_text(encoding='utf-8')
    return None


def has_jd(job_id: str) -> bool:
    """Check if JD is already parsed"""
    jd_file = JD_DIR / f"{job_id}.txt"
    return jd_file.exists()


# Test
if __name__ == "__main__":
    # Test with Abnormal Security
    result = parse_and_store_jd(
        job_id="gh_7551356003",
        url="https://careers.abnormalsecurity.com/jobs/7551356003",
        title="Senior Product Manager",
        company="Abnormal Security",
        ats="greenhouse"
    )
    print(json.dumps(result, indent=2))
