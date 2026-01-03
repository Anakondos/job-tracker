"""
Ollama integration for Job Tracker.
Provides AI-powered classification, parsing fixes, and autonomous problem solving.
"""

import requests
import json
from typing import Optional


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"


def ollama_request(prompt: str, system: str = None, temperature: float = 0.1) -> Optional[str]:
    """Make a request to Ollama API."""
    try:
        payload = {
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
            }
        }
        if system:
            payload["system"] = system
        
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
        else:
            print(f"Ollama error: {resp.status_code}")
            return None
    except Exception as e:
        print(f"Ollama connection error: {e}")
        return None


def classify_role_ai(title: str, description: str = "") -> dict:
    """
    Use AI to classify job role when rule-based classification fails.
    Returns: {role_family, role_id, confidence, reason}
    """
    system = """You are a job role classifier. Classify jobs into these categories:
- product: Product Manager, Product Owner, Product Lead
- tpm_program: Technical Program Manager, Program Manager, TPM
- project: Project Manager, Project Lead, Delivery Manager
- other: Not a PM/TPM/Project role

Respond ONLY with JSON: {"role_family": "...", "role_id": "...", "confidence": 0-100, "reason": "..."}"""

    prompt = f"""Classify this job:
Title: {title}
Description: {description[:500] if description else 'N/A'}

JSON response:"""

    response = ollama_request(prompt, system)
    
    if response:
        try:
            # Extract JSON from response
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass
    
    return {
        "role_family": "other",
        "role_id": None,
        "confidence": 50,
        "reason": "AI classification failed"
    }


def extract_job_details_ai(html_text: str) -> dict:
    """
    Use AI to extract job details from raw HTML/text when parsing fails.
    """
    system = """Extract job posting details from the text.
Respond ONLY with JSON: {"title": "...", "company": "...", "location": "...", "salary": "..."}
If a field is not found, use null."""

    prompt = f"""Extract job details from this text:

{html_text[:2000]}

JSON response:"""

    response = ollama_request(prompt, system)
    
    if response:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass
    
    return {}


def fix_company_name(raw_name: str, url: str = "") -> str:
    """
    Use AI to fix/normalize company name from URL or raw extraction.
    """
    system = """You are a company name normalizer. Given a raw company name or URL, return the proper company name.
Examples:
- "capitalonecareers" -> "Capital One"
- "jobs.lever.co/stripe" -> "Stripe"
- "microsoftcareers" -> "Microsoft"

Respond with ONLY the company name, nothing else."""

    prompt = f"""Normalize this company name:
Raw: {raw_name}
URL: {url}

Company name:"""

    response = ollama_request(prompt, system, temperature=0)
    
    if response and len(response) < 100:
        # Clean up response
        return response.strip().strip('"').strip("'")
    
    return raw_name


def match_job_to_cv(job_title: str, job_description: str, cv_summary: str) -> dict:
    """
    Score how well a job matches the user's CV.
    Returns: {score: 0-100, reasons: [...], recommendations: [...]}
    """
    system = """You are a job matching expert. Analyze how well a job matches a candidate's CV.
Score from 0-100 where:
- 90-100: Excellent match, highly relevant
- 70-89: Good match, mostly relevant
- 50-69: Partial match, some relevant experience
- Below 50: Poor match

Respond ONLY with JSON: {"score": N, "reasons": ["..."], "recommendations": ["..."]}"""

    prompt = f"""Match this job to the CV:

JOB:
Title: {job_title}
Description: {job_description[:1000] if job_description else 'N/A'}

CV SUMMARY:
{cv_summary[:1000]}

JSON response:"""

    response = ollama_request(prompt, system, temperature=0.3)
    
    if response:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass
    
    return {"score": 50, "reasons": ["Could not analyze"], "recommendations": []}


def generate_cover_letter_points(job_title: str, company: str, job_description: str, cv_highlights: str) -> list:
    """
    Generate key points for a cover letter based on job and CV.
    """
    system = """You are a cover letter expert. Generate 3 SHORT bullet points (max 20 words each) that:
1. Connect candidate's SPECIFIC experience to job requirements
2. Include concrete numbers/metrics when possible
3. Show genuine interest in THIS company (not generic)

Rules:
- Each bullet must be ONE sentence, under 20 words
- No generic phrases like "drive innovation", "stay ahead of the curve"
- No repeating company name in every bullet
- Focus on RESULTS and IMPACT, not responsibilities

Respond ONLY with JSON: {"points": ["point 1", "point 2", "point 3"]}"""

    prompt = f"""Generate 3 cover letter bullets:

JOB: {job_title} at {company}
Description: {job_description[:600] if job_description else 'N/A'}

CANDIDATE:
{cv_highlights[:400]}

JSON (3 short bullets):"""

    response = ollama_request(prompt, system, temperature=0.5)
    
    if response:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                return data.get("points", [])
        except json.JSONDecodeError:
            pass
    
    return []


def select_cv_for_role(job_title: str, available_cvs: list) -> str:
    """
    Use AI to select the best CV for a job role.
    
    Args:
        job_title: Job title to match
        available_cvs: List of CV file paths
        
    Returns:
        Path to the best matching CV
    """
    if not available_cvs:
        return None
    
    if len(available_cvs) == 1:
        return available_cvs[0]
    
    system = """You are a CV selector. Given a job title and available CVs, select the best match.

CV naming patterns:
- TPM_CV, TPM = Technical Program Manager roles
- Product_Manager_CV, PM_CV = Product Manager roles  
- Project_Manager_CV = Project Manager roles
- Scrum_Master_CV = Scrum Master/Agile roles
- DeliveryLead_CV = Delivery Lead roles
- PO_CV = Product Owner roles

Respond ONLY with the index number (0, 1, 2, etc.) of the best CV."""

    cv_list = "\n".join([f"{i}: {cv.split('/')[-1]}" for i, cv in enumerate(available_cvs)])
    
    prompt = f"""Select best CV for this job:

JOB TITLE: {job_title}

AVAILABLE CVs:
{cv_list}

Best CV index:"""

    response = ollama_request(prompt, system, temperature=0)
    
    if response:
        try:
            # Extract number from response
            idx = int(''.join(filter(str.isdigit, response.split()[0])))
            if 0 <= idx < len(available_cvs):
                return available_cvs[idx]
        except (ValueError, IndexError):
            pass
    
    # Default: return first CV with matching keyword
    title_lower = job_title.lower()
    for cv in available_cvs:
        cv_lower = cv.lower()
        if 'tpm' in title_lower and 'tpm' in cv_lower:
            return cv
        if 'product' in title_lower and 'product' in cv_lower:
            return cv
        if 'project' in title_lower and 'project' in cv_lower:
            return cv
        if 'program' in title_lower and ('tpm' in cv_lower or 'program' in cv_lower):
            return cv
    
    return available_cvs[0]


def generate_cover_letter(company: str, position: str, job_description: str = "", profile_summary: str = "") -> str:
    """
    Generate a personalized cover letter using AI.
    
    Returns:
        Cover letter text
    """
    system = """You are a professional cover letter writer. Write concise, compelling cover letters.

Rules:
- Keep it under 300 words
- Be specific about why this company and role
- Highlight 2-3 relevant achievements
- Show enthusiasm without being generic
- Professional but warm tone
- End with a clear call to action

Format:
Dear Hiring Manager,

[Opening paragraph - why this role/company]

[Middle paragraph - relevant experience and achievements]

[Closing paragraph - call to action]

Sincerely,
[Name]"""

    prompt = f"""Write a cover letter for:

COMPANY: {company}
POSITION: {position}
JOB DESCRIPTION: {job_description[:1000] if job_description else 'Not provided'}

CANDIDATE SUMMARY:
{profile_summary[:800] if profile_summary else 'Experienced Technical Program Manager with 9+ years in fintech and banking.'}

Cover letter:"""

    response = ollama_request(prompt, system, temperature=0.7)
    
    return response if response else f"""Dear Hiring Manager,

I am writing to express my strong interest in the {position} position at {company}.

With over 9 years of experience in program management within fintech and banking sectors, I bring a proven track record of delivering complex technical programs on time and within budget.

I would welcome the opportunity to discuss how my background aligns with your team's needs.

Sincerely,
Anton Kondakov"""


def is_ollama_available() -> bool:
    """Check if Ollama server is running."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        return resp.status_code == 200
    except:
        return False


def verify_form_fields(page_html: str, expected_fields: dict) -> dict:
    """
    Use AI to verify that form fields are correctly filled.
    
    Args:
        page_html: HTML content of the form page
        expected_fields: Dict of field names and expected values
        
    Returns:
        {"ok": bool, "filled": [...], "missing": [...], "incorrect": [...], "suggestions": [...]}
    """
    system = """You are a form validation assistant. Analyze the HTML form and check if fields are filled correctly.

For each expected field, check:
1. Is the field present in the form?
2. Is it filled with the expected value or similar?
3. If not filled, what value is shown?

Respond ONLY with JSON:
{
    "ok": true/false,
    "filled": [{"field": "...", "value": "..."}],
    "missing": [{"field": "...", "expected": "..."}],
    "incorrect": [{"field": "...", "expected": "...", "actual": "..."}],
    "suggestions": ["..."]
}"""

    # Truncate HTML but keep form elements
    html_truncated = page_html[:8000] if len(page_html) > 8000 else page_html
    
    prompt = f"""Check if these fields are filled in the form:

EXPECTED FIELDS:
{json.dumps(expected_fields, indent=2)}

FORM HTML:
{html_truncated}

JSON response:"""

    response = ollama_request(prompt, system, temperature=0.1)
    
    if response:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass
    
    return {
        "ok": False,
        "filled": [],
        "missing": [],
        "incorrect": [],
        "suggestions": ["Could not verify form - AI check failed"]
    }


def suggest_form_fixes(page_html: str, profile_data: dict) -> list:
    """
    Use AI to suggest what fields need to be filled and how.
    
    Returns list of actions: [{"action": "fill", "field": "...", "value": "...", "selector": "..."}]
    """
    system = """You are a form automation assistant. Analyze the HTML form and suggest what fields need to be filled.

For each unfilled or incorrectly filled field:
1. Identify the field (by label, id, or name)
2. Suggest the value from the profile
3. Provide a CSS selector to find it

Respond ONLY with JSON:
{
    "actions": [
        {"action": "fill", "field": "field_name", "value": "value_to_fill", "selector": "css_selector"},
        {"action": "select", "field": "dropdown_name", "value": "option_text", "selector": "css_selector"}
    ]
}"""

    html_truncated = page_html[:6000] if len(page_html) > 6000 else page_html
    
    prompt = f"""Analyze this form and suggest fills:

PROFILE DATA:
{json.dumps(profile_data, indent=2)[:2000]}

FORM HTML:
{html_truncated}

JSON response:"""

    response = ollama_request(prompt, system, temperature=0.2)
    
    if response:
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                data = json.loads(response[start:end])
                return data.get("actions", [])
        except json.JSONDecodeError:
            pass
    
    return []


# Test
if __name__ == "__main__":
    print(f"Ollama available: {is_ollama_available()}")
    
    if is_ollama_available():
        # Test classification
        result = classify_role_ai("Strategic Project Lead", "Lead cross-functional teams...")
        print(f"Classification: {result}")
        
        # Test company name fix
        fixed = fix_company_name("capitalonecareers", "https://www.capitalonecareers.com")
        print(f"Company name: {fixed}")


def generate_company_mission(company: str, job_description: str = "", position: str = "") -> str:
    """
    Generate a personalized sentence about why the candidate wants to work at this company.
    Based on company name, job description, and position.
    
    Returns something like:
    "I'm particularly drawn to Coinbase's mission to increase economic freedom - 
    a vision I'm eager to contribute to through my fintech experience."
    """
    prompt = f"""Generate ONE sentence (30-50 words) explaining why a candidate is excited to work at {company}.

Company: {company}
Position: {position}
Job Description excerpt: {job_description[:500] if job_description else 'Not provided'}

Requirements:
- Start with "I'm particularly drawn to" or "I'm excited about" or similar
- Mention something specific about the company's mission, product, or impact
- Connect it to the candidate's desire to contribute
- Keep it genuine and professional, not generic
- ONE sentence only, no quotes

Example good outputs:
- "I'm particularly drawn to Stripe's mission to increase the GDP of the internet, and I'm eager to contribute my payments platform experience to this vision."
- "I'm excited about Airbnb's focus on creating belonging anywhere, which aligns with my passion for building products that connect people globally."

Generate the sentence:"""

    system = "You are a career coach helping write compelling cover letters. Be specific and genuine, avoid generic phrases."
    
    result = ollama_request(prompt, system, temperature=0.7)
    
    if result:
        # Clean up the result
        result = result.strip().strip('"').strip("'")
        # Ensure it starts appropriately
        if not any(result.lower().startswith(s) for s in ["i'm", "i am", "what"]):
            result = "I'm particularly drawn to " + result
        return result
    
    # Fallback
    return f"I'm excited about the opportunity to contribute to {company}'s continued growth and innovation."

