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
    system = """Generate 3-5 key talking points for a cover letter that connects the candidate's experience to the job requirements.
Be specific and actionable.

Respond ONLY with JSON: {"points": ["point 1", "point 2", ...]}"""

    prompt = f"""Generate cover letter points:

JOB: {job_title} at {company}
Description: {job_description[:800] if job_description else 'N/A'}

CANDIDATE HIGHLIGHTS:
{cv_highlights[:500]}

JSON response:"""

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


def is_ollama_available() -> bool:
    """Check if Ollama server is running."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        return resp.status_code == 200
    except:
        return False


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
