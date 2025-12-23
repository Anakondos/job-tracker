# utils/job_utils.py
"""
Job utilities: ID generation, role classification, similarity detection
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).parent.parent / "config"


def generate_job_id(job: dict) -> str:
    """
    Генерирует уникальный ID вакансии.
    ATS сам создаёт разные ID для разных локаций.
    
    Format: {ats_prefix}_{ats_job_id}
    Examples: gh_7374078, lv_abc123, sr_f8a2b3c1
    """
    ats = job.get("ats", "unknown")
    ats_job_id = job.get("ats_job_id")
    
    # Map ATS to 2-letter prefix
    ats_prefix = {
        "greenhouse": "gh",
        "lever": "lv",
        "smartrecruiters": "sr",
        "workday": "wd",
    }.get(ats, "xx")
    
    if ats_job_id:
        return f"{ats_prefix}_{ats_job_id}"
    
    # Fallback: hash from URL
    url = job.get("url", "")
    if url:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        return f"{ats_prefix}_{url_hash}"
    
    # Last resort: hash from company+title+location
    composite = f"{job.get('company', '')}|{job.get('title', '')}|{job.get('location', '')}"
    composite_hash = hashlib.md5(composite.encode()).hexdigest()[:8]
    return f"{ats_prefix}_{composite_hash}"


def load_roles_config() -> dict:
    """Load roles.json configuration"""
    roles_path = CONFIG_DIR / "roles.json"
    if not roles_path.exists():
        return {"target_roles": [], "skip_roles": {}}
    
    with roles_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(text: str) -> str:
    """Normalize text for comparison"""
    if not text:
        return ""
    # Lowercase, remove extra spaces
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def keyword_in_text(keyword: str, text: str) -> bool:
    """
    Check if keyword exists in text as a word/phrase (not substring).
    E.g., 'pm' should match 'Senior PM' but not 'Development Manager'.
    """
    if not keyword or not text:
        return False
    
    keyword = keyword.lower()
    text = text.lower()
    
    # For short keywords (2-3 chars like 'pm', 'po', 'tpm'), use word boundary
    if len(keyword) <= 3:
        pattern = r'\b' + re.escape(keyword) + r'\b'
        return bool(re.search(pattern, text))
    
    # For longer keywords, simple substring is fine
    return keyword in text


def classify_role(title: Optional[str], description: Optional[str] = None) -> dict:
    """
    Классифицирует роль на основе title и description.
    Использует roles.json для конфигурации.
    
    Returns:
        {
            "role_family": "product" | "tpm_program" | "project" | "other",
            "role_id": "product_manager" | None,
            "confidence": 0-100,
            "reason": str,
            "excluded": bool,
            "exclude_reason": str | None
        }
    """
    if not title:
        return {
            "role_family": "other",
            "role_id": None,
            "confidence": 0,
            "reason": "No title provided",
            "excluded": False,
            "exclude_reason": None
        }
    
    title_lower = normalize_text(title)
    desc_lower = normalize_text(description) if description else ""
    
    config = load_roles_config()
    target_roles = config.get("target_roles", [])
    skip_roles = config.get("skip_roles", {})
    
    # Step 1: Check skip_roles (negative keywords)
    for category, keywords in skip_roles.items():
        for keyword in keywords:
            if keyword_in_text(keyword, title_lower):
                return {
                    "role_family": "other",
                    "role_id": None,
                    "confidence": 95,
                    "reason": f"Skip role: {category} (matched '{keyword}')",
                    "excluded": True,
                    "exclude_reason": f"Matched skip keyword: {keyword}"
                }
    
    # Step 2: Check target_roles (sorted by priority)
    sorted_roles = sorted(target_roles, key=lambda r: r.get("priority", 0), reverse=True)
    
    for role in sorted_roles:
        role_id = role.get("id", "")
        keywords_title = role.get("keywords_title", [])
        keywords_desc = role.get("keywords_description", [])
        exclude_keywords = role.get("exclude_keywords", [])
        
        # Check title keywords first
        title_match = None
        for kw in keywords_title:
            if keyword_in_text(kw, title_lower):
                title_match = kw
                break
        
        if not title_match:
            continue  # No title match, try next role
        
        # Check exclude_keywords (e.g., "construction" for project manager)
        excluded = False
        exclude_match = None
        for exc_kw in exclude_keywords:
            if keyword_in_text(exc_kw, title_lower) or keyword_in_text(exc_kw, desc_lower):
                excluded = True
                exclude_match = exc_kw
                break
        
        if excluded:
            # Title matches but excluded by keyword
            return {
                "role_family": "other",
                "role_id": role_id,
                "confidence": 90,
                "reason": f"Matched '{title_match}' but excluded by '{exclude_match}'",
                "excluded": True,
                "exclude_reason": f"Contains excluded keyword: {exclude_match}"
            }
        
        # Title matched and not excluded - this is a match!
        if title_match:
            # Determine role_family from role_id
            role_family = _get_role_family(role_id)
            
            return {
                "role_family": role_family,
                "role_id": role_id,
                "confidence": 95,
                "reason": f"Matched title keyword: '{title_match}'",
                "excluded": False,
                "exclude_reason": None
            }
        
        # Check description keywords (lower confidence)
        if desc_lower:
            desc_match_count = sum(1 for kw in keywords_desc if keyword_in_text(kw, desc_lower))
            if desc_match_count >= 2:  # Need at least 2 description keywords
                role_family = _get_role_family(role_id)
                
                return {
                    "role_family": role_family,
                    "role_id": role_id,
                    "confidence": 70,
                    "reason": f"Matched {desc_match_count} description keywords for {role_id}",
                    "excluded": False,
                    "exclude_reason": None
                }
    
    # Step 3: No match found
    return {
        "role_family": "other",
        "role_id": None,
        "confidence": 50,
        "reason": "No matching role keywords found",
        "excluded": False,
        "exclude_reason": None
    }


def _get_role_family(role_id: str) -> str:
    """Map role_id to role_family"""
    if not role_id:
        return "other"
    
    role_id_lower = role_id.lower()
    
    # Product family
    if any(x in role_id_lower for x in ["product_manager", "product_owner", "director_product", "product_analyst", "product_operations"]):
        return "product"
    
    # TPM/Program family
    if any(x in role_id_lower for x in ["program_manager", "technical_program", "tpm", "director_program", "strategy_operations", "scrum_master"]):
        return "tpm_program"
    
    # Project family
    if "project" in role_id_lower:
        return "project"
    
    return "other"


def calculate_similarity(title1: str, title2: str) -> float:
    """
    Calculate similarity between two job titles.
    Returns 0.0 to 1.0
    """
    t1 = normalize_text(title1)
    t2 = normalize_text(title2)
    
    if not t1 or not t2:
        return 0.0
    
    if t1 == t2:
        return 1.0
    
    # Simple word overlap similarity
    words1 = set(t1.split())
    words2 = set(t2.split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1 & words2
    union = words1 | words2
    
    return len(intersection) / len(union)


def find_similar_jobs(job: dict, archive: list, threshold: float = 0.8) -> list:
    """
    Find similar jobs in archive (same company, similar title).
    Used to detect re-posted positions.
    """
    similar = []
    job_company = normalize_text(job.get("company", ""))
    job_title = job.get("title", "")
    
    for archived in archive:
        archived_company = normalize_text(archived.get("company", ""))
        
        # Must be same company
        if job_company != archived_company:
            continue
        
        archived_title = archived.get("title", "")
        similarity = calculate_similarity(job_title, archived_title)
        
        if similarity >= threshold:
            similar.append({
                "job": archived,
                "similarity": similarity
            })
    
    # Sort by similarity descending
    similar.sort(key=lambda x: x["similarity"], reverse=True)
    
    return similar
