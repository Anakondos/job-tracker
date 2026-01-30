"""
Comprehensive Application Preparation API
"""

import os
import re
import json
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field


GOLD_CV_PATH = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV")
APPLICATIONS_PATH = GOLD_CV_PATH / "Applications"

ROLE_CV_MAP = {
    "product": "CV_Anton_Kondakov_Product Manager.docx",
    "tpm_program": "CV_Anton_Kondakov_TPM.docx", 
    "project": "CV_Anton_Kondakov_Project Manager.docx",
    "other": "CV_Anton_Kondakov_Product Manager.docx",
}


@dataclass
class PrepareResult:
    ok: bool = True
    error: str = None
    match_score: int = 0
    fit_level: str = "unknown"
    analysis_summary: str = ""
    key_requirements: List[str] = field(default_factory=list)
    matching_experience: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    cv_decision: str = "base"
    cv_reason: str = ""
    cv_path: str = None
    cv_filename: str = None
    keywords_added: List[str] = field(default_factory=list)
    cover_letter_path: str = None
    cover_letter_filename: str = None
    cover_letter_preview: str = None
    application_url: str = None
    application_folder: str = None
    
    def to_dict(self):
        return {
            "ok": self.ok, "error": self.error,
            "match_score": self.match_score, "fit_level": self.fit_level,
            "analysis_summary": self.analysis_summary,
            "key_requirements": self.key_requirements or [],
            "matching_experience": self.matching_experience or [],
            "gaps": self.gaps or [], "red_flags": self.red_flags or [],
            "cv_decision": self.cv_decision, "cv_reason": self.cv_reason,
            "cv_path": self.cv_path, "cv_filename": self.cv_filename,
            "keywords_added": self.keywords_added or [],
            "cover_letter_path": self.cover_letter_path,
            "cover_letter_filename": self.cover_letter_filename,
            "cover_letter_preview": self.cover_letter_preview,
            "application_url": self.application_url,
            "application_folder": self.application_folder,
        }


def call_claude_api(prompt: str, max_tokens: int = 2000) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("[PrepareApp] No ANTHROPIC_API_KEY")
        return None
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
            timeout=60
        )
        if resp.status_code != 200:
            print(f"[PrepareApp] API error: {resp.status_code}")
            return None
        return resp.json().get("content", [{}])[0].get("text", "")
    except Exception as e:
        print(f"[PrepareApp] Exception: {e}")
        return None


CANDIDATE_PROFILE = """
CANDIDATE: Anton Kondakov - Senior Product Manager / Product Owner
EXPERIENCE: 15+ years in Product Management, Program Management, TPM
KEY ACHIEVEMENTS: Cloud migration ($1.2M savings), Regulatory platforms (MiFID II, CAT, EMIR), ML-powered analytics (88% efficiency)
TECHNICAL: AWS, GCP, Azure, JIRA, Confluence, SQL, Agile, SAFe, Scrum
CERTIFICATIONS: SAFe POPM, PSM I, Google Cloud Digital Leader
LOCATION: Raleigh, NC | WORK AUTH: Authorized, no sponsorship needed
"""


def analyze_job_with_ai(job_title: str, company: str, jd: str, role_family: str) -> Dict:
    prompt = f"""{CANDIDATE_PROFILE}

JOB: {company} - {job_title} ({role_family})
JD: {jd[:5000]}

Analyze and return ONLY valid JSON:
{{"match_score": <0-100>, "fit_level": "<excellent|good|moderate|low>", "analysis_summary": "<2-3 sentences>", "key_requirements": ["<top 5>"], "matching_experience": ["<matches>"], "gaps": ["<gaps>"], "red_flags": ["<concerns>"], "cv_decision": "<base|optimize>", "cv_reason": "<why>", "keywords_to_add": ["<missing keywords>"], "cover_letter_focus": ["<points>"]}}"""

    response = call_claude_api(prompt, 1500)
    if not response:
        return {"error": "AI failed"}
    try:
        match = re.search(r'\{[\s\S]*\}', response)
        if match:
            return json.loads(match.group())
    except:
        pass
    return {"error": "Parse failed"}


def generate_cover_letter(job_title: str, company: str, jd: str, analysis: Dict) -> str:
    focus = analysis.get("cover_letter_focus", [])
    prompt = f"""{CANDIDATE_PROFILE}

JOB: {company} - {job_title}
FOCUS: {', '.join(focus)}
JD: {jd[:2000]}

Write a professional 3-4 paragraph cover letter. No generic phrases. Be specific."""
    return call_claude_api(prompt, 1000) or ""


def create_optimized_cv(job_title: str, company: str, role_family: str, keywords: List[str]) -> Optional[Path]:
    try:
        from docx import Document
        cv_filename = ROLE_CV_MAP.get(role_family, "CV_Anton_Kondakov_Product Manager.docx")
        cv_path = GOLD_CV_PATH / cv_filename
        if not cv_path.exists():
            return None
        
        safe_company = re.sub(r'[^\w\s-]', '', company).strip().replace(' ', '_')
        safe_position = re.sub(r'[^\w\s-]', '', job_title).strip().replace(' ', '_')[:40]
        folder_name = f"{safe_company}_{safe_position}_{datetime.now().strftime('%Y%m%d')}"
        app_folder = APPLICATIONS_PATH / folder_name
        app_folder.mkdir(parents=True, exist_ok=True)
        
        doc = Document(cv_path)
        if keywords:
            for i, para in enumerate(doc.paragraphs):
                if "COMPETENCIES" in para.text.upper():
                    for j in range(i+1, min(i+15, len(doc.paragraphs))):
                        if "technical" in doc.paragraphs[j].text.lower():
                            current = doc.paragraphs[j].text.rstrip('.')
                            doc.paragraphs[j].clear()
                            doc.paragraphs[j].add_run(f"{current} [+Added: {', '.join(keywords[:5])}]")
                            break
                    break
        
        output_path = app_folder / f"CV_Anton_Kondakov_{safe_company}.docx"
        doc.save(output_path)
        return output_path
    except Exception as e:
        print(f"[PrepareApp] CV error: {e}")
        return None


def save_cover_letter(company: str, content: str, app_folder: Path) -> Optional[Path]:
    try:
        safe_company = re.sub(r'[^\w\s-]', '', company).strip().replace(' ', '_')
        output_path = app_folder / f"Cover_Letter_{safe_company}.txt"
        output_path.write_text(content)
        return output_path
    except Exception as e:
        print(f"[PrepareApp] CL error: {e}")
        return None


def prepare_application(job_title: str, company: str, job_url: str, jd: str, role_family: str = "product") -> PrepareResult:
    result = PrepareResult()
    print(f"[PrepareApp] Starting: {company} - {job_title}")
    
    if not jd or len(jd) < 100:
        result.ok = False
        result.error = "Job description too short"
        result.application_url = job_url
        return result
    
    # Step 1: AI Analysis
    print("[PrepareApp] Step 1: AI Analysis...")
    analysis = analyze_job_with_ai(job_title, company, jd, role_family)
    
    if "error" in analysis:
        result.match_score = 70
        result.fit_level = "moderate"
        result.analysis_summary = "AI unavailable, using defaults"
        result.cv_decision = "base"
        analysis = {}
    else:
        result.match_score = analysis.get("match_score", 70)
        result.fit_level = analysis.get("fit_level", "moderate")
        result.analysis_summary = analysis.get("analysis_summary", "")
        result.key_requirements = analysis.get("key_requirements", [])
        result.matching_experience = analysis.get("matching_experience", [])
        result.gaps = analysis.get("gaps", [])
        result.red_flags = analysis.get("red_flags", [])
        result.cv_decision = analysis.get("cv_decision", "base")
        result.cv_reason = analysis.get("cv_reason", "")
    
    # Step 2: CV
    print(f"[PrepareApp] Step 2: CV ({result.cv_decision})...")
    keywords = analysis.get("keywords_to_add", [])
    
    if result.cv_decision == "optimize" and keywords:
        cv_path = create_optimized_cv(job_title, company, role_family, keywords)
        if cv_path:
            result.cv_path = str(cv_path)
            result.cv_filename = cv_path.name
            result.keywords_added = keywords[:5]
            result.application_folder = str(cv_path.parent)
    
    if not result.cv_path:
        cv_filename = ROLE_CV_MAP.get(role_family, "CV_Anton_Kondakov_Product Manager.docx")
        base_cv = GOLD_CV_PATH / cv_filename
        if base_cv.exists():
            result.cv_path = str(base_cv)
            result.cv_filename = cv_filename
    
    # Step 3: Cover Letter
    print("[PrepareApp] Step 3: Cover Letter...")
    cl_text = generate_cover_letter(job_title, company, jd, analysis)
    
    if cl_text:
        if not result.application_folder:
            safe_company = re.sub(r'[^\w\s-]', '', company).strip().replace(' ', '_')
            safe_position = re.sub(r'[^\w\s-]', '', job_title).strip().replace(' ', '_')[:40]
            folder_name = f"{safe_company}_{safe_position}_{datetime.now().strftime('%Y%m%d')}"
            app_folder = APPLICATIONS_PATH / folder_name
            app_folder.mkdir(parents=True, exist_ok=True)
            result.application_folder = str(app_folder)
        
        cl_path = save_cover_letter(company, cl_text, Path(result.application_folder))
        if cl_path:
            result.cover_letter_path = str(cl_path)
            result.cover_letter_filename = cl_path.name
            result.cover_letter_preview = cl_text[:500] + "..." if len(cl_text) > 500 else cl_text
    
    result.application_url = job_url
    
    # Save metadata.json with keywords and analysis for future reference
    if result.application_folder:
        try:
            metadata = {
                "job_title": job_title,
                "company": company,
                "job_url": job_url,
                "role_family": role_family,
                "match_score": result.match_score,
                "fit_level": result.fit_level,
                "cv_decision": result.cv_decision,
                "cv_reason": result.cv_reason,
                "keywords_added": result.keywords_added or [],
                "gaps": result.gaps or [],
                "red_flags": result.red_flags or [],
                "created_at": datetime.now().isoformat()
            }
            metadata_path = Path(result.application_folder) / "metadata.json"
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            print(f"[PrepareApp] Metadata save error: {e}")
    
    print(f"[PrepareApp] Done: score={result.match_score}")
    return result
