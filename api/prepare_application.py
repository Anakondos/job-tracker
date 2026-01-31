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


GOLD_CV_PATH = Path("/Users/anton/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV")
# Fallback for other machine
if not GOLD_CV_PATH.exists():
    GOLD_CV_PATH = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV")
    
APPLICATIONS_PATH = GOLD_CV_PATH / "Applications"

ROLE_CV_MAP = {
    "product": "CV_Anton_Kondakov_Product Manager.docx",
    "tpm_program": "CV_Anton_Kondakov_TPM.docx", 
    "project": "CV_Anton_Kondakov_Project Manager.docx",
    "scrum": "CV_Anton_Kondakov_Scrum Master.docx",
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


def extract_cv_text(cv_path: Path) -> str:
    """Extract text from DOCX CV file for RAG"""
    try:
        from docx import Document
        doc = Document(str(cv_path))
        text_parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                text_parts.append(para.text.strip())
        # Also get tables (often used in CVs)
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    text_parts.append(row_text)
        return '\n'.join(text_parts)
    except Exception as e:
        print(f"[PrepareApp] CV extraction error: {e}")
        return ""


def get_cv_for_role(role_family: str, job_title: str = "") -> tuple:
    """Get CV path and text for given role family, with title-based detection"""
    
    # Check if job title indicates specific role type
    title_lower = job_title.lower() if job_title else ""
    
    # Scrum Master detection - use Scrum CV even if role_family is tpm_program
    if any(x in title_lower for x in ["scrum master", "scrum coach", "agile coach", "agile delivery"]):
        cv_name = "CV_Anton_Kondakov_Scrum Master.docx"
        print(f"[PrepareApp] Detected Scrum Master role from title")
    # Product Owner detection
    elif "product owner" in title_lower:
        cv_name = "CV_Anton_Kondakov_PO.docx"
        print(f"[PrepareApp] Detected Product Owner role from title")
    # Delivery Manager/Lead detection  
    elif any(x in title_lower for x in ["delivery manager", "delivery lead"]):
        cv_name = "CV_Anton_Kondakov_DeliveryLead.docx"
        print(f"[PrepareApp] Detected Delivery role from title")
    else:
        cv_name = ROLE_CV_MAP.get(role_family, ROLE_CV_MAP["product"])
    
    cv_path = GOLD_CV_PATH / cv_name
    
    if cv_path.exists():
        cv_text = extract_cv_text(cv_path)
        return cv_path, cv_text
    
    # Fallback to product CV
    cv_path = GOLD_CV_PATH / ROLE_CV_MAP["product"]
    if cv_path.exists():
        return cv_path, extract_cv_text(cv_path)
    
    return None, ""


# Comprehensive candidate profile (used when CV extraction fails)
CANDIDATE_PROFILE = """
CANDIDATE: Anton Kondakov
TITLE: Senior Product Manager / Technical Program Manager / Product Owner / Scrum Master

EXPERIENCE SUMMARY (15+ years):
- Product Management: 9+ years leading B2B SaaS products, enterprise platforms, regulatory systems
- Technical Program Management: 5+ years managing complex cross-functional programs
- Scrum Master / Agile Coach: 9+ years facilitating Agile teams, SAFe implementation
- Team Leadership: Managed teams of 5-15 across multiple time zones

KEY COMPANIES:
- Deutsche Bank (VP, Product Owner) - RegTech, MiFID II, CAT reporting
- UBS (Senior Business Analyst) - Trade reporting, EMIR compliance
- Barclays Capital - Financial data systems
- Current: Senior Product Manager (AI/ML platforms)

MAJOR ACHIEVEMENTS:
- Cloud Migration: Led AWS migration saving $1.2M annually, 40% latency reduction
- Regulatory Platforms: Built MiFID II/CAT/EMIR compliant reporting (100% regulatory compliance)
- ML Analytics: Developed predictive models achieving 88% forecast accuracy
- API Platform: Designed REST APIs serving 50M+ daily requests
- Cost Optimization: Reduced infrastructure costs by $500K through optimization

TECHNICAL SKILLS:
- Cloud: AWS (certified path), GCP, Azure
- Tools: JIRA (advanced admin), Confluence, ServiceNow, Datadog, Splunk
- Data: SQL, Python, Tableau, Power BI
- CI/CD: Jenkins, GitHub Actions, ArgoCD
- AI/ML: Amazon Q Developer, ML model deployment

CERTIFICATIONS:
- SAFe 5 POPM (Product Owner/Product Manager)
- PSM I (Professional Scrum Master)
- Google Cloud Digital Leader
- AWS Cloud Practitioner (in progress)

METHODOLOGIES:
- Agile/Scrum (9+ years), SAFe (PI Planning, ART sync), Kanban
- SDLC, DevOps practices, SRE principles

LOCATION: Raleigh, NC (Open to Charlotte, RTP, Remote USA)
WORK AUTHORIZATION: US Authorized, no sponsorship required
AVAILABILITY: Immediate

LOOKING FOR:
- Senior PM, TPM, Program Manager, Product Owner, Scrum Master roles
- Fintech, Enterprise SaaS, B2B platforms, RegTech
- Companies with strong engineering culture
- Remote or NC-based positions
"""


def analyze_job_with_ai(job_title: str, company: str, jd: str, role_family: str) -> Dict:
    """Analyze job match using RAG - loads actual CV for comparison"""
    
    # Try to load actual CV for this role type (pass job_title for better detection)
    cv_path, cv_text = get_cv_for_role(role_family, job_title)
    
    # Use CV text if available, otherwise use static profile
    if cv_text and len(cv_text) > 500:
        candidate_info = f"""ANTON'S CV ({role_family.upper()} version):
{cv_text[:4000]}

ADDITIONAL CONTEXT:
- Location: Raleigh, NC (Open to Charlotte, RTP, Remote USA)
- Work Authorization: US Authorized, no sponsorship required
- Certifications: SAFe 5 POPM, PSM I, Google Cloud Digital Leader
- Looking for: Senior PM, TPM, Program Manager, Product Owner, Scrum Master roles
"""
        print(f"[PrepareApp] Using CV from: {cv_path.name} ({len(cv_text)} chars)")
    else:
        candidate_info = CANDIDATE_PROFILE
        print(f"[PrepareApp] Using static profile (CV not found or too short)")
    
    prompt = f"""{candidate_info}

JOB: {company} - {job_title} ({role_family})
JD: {jd[:5000]}

Analyze this job for Anton. Compare JD requirements against his ACTUAL CV above.

Return ONLY valid JSON:
{{
  "match_score": <0-100 based on REAL experience match from CV>,
  "fit_level": "<excellent|good|moderate|low>",
  "analysis_summary": "<2-3 sentences - be specific about what matches/doesn't from CV>",
  "role_type": "<actual role type: Scrum Master, Product Manager, TPM, etc>",
  "location_info": "<location, remote status, salary if mentioned>",
  "key_requirements": [
    {{"requirement": "<from JD>", "anton_has": "<yes|partial|no>", "comment": "<cite CV evidence>"}}
  ],
  "matching_experience": ["<specific CV items that match>"],
  "gaps": ["<what's missing vs JD>"],
  "red_flags": ["<concerns>"],
  "pros": ["<reasons to apply>"],
  "cons": ["<reasons to skip>"],
  "recommendation": "<STRONG APPLY|APPLY|MAYBE|SKIP>",
  "recommendation_reason": "<why>",
  "cv_decision": "<base|optimize>",
  "cv_reason": "<why>",
  "keywords_to_add": ["<keywords from JD not in CV>"],
  "cover_letter_focus": ["<points to emphasize>"]
}}

IMPORTANT: Base match_score on ACTUAL CV content, not assumptions. If Anton's CV shows 9+ years Scrum Master experience and job needs 5+ years, that's a YES match."""

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
