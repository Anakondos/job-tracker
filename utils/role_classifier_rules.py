"""
Rule-based job role classifier
"""
import json
from pathlib import Path
from typing import Dict, Optional

CONFIG_DIR = Path(__file__).parent.parent / "config"

def normalize_text(text: str) -> str:
    if not text:
        return ""
    return text.lower().strip()

def classify_job_rule_based(title: str, description: str = "") -> Dict:
    """Simple rule-based classifier - returns same format as old classify_role"""
    title_lower = normalize_text(title)
    
    # Check for Product Manager
    if "product manager" in title_lower or " pm " in title_lower:
        return {
            "role_family": "product",
            "confidence": 90,
            "reason": "Product Manager role"
        }
    
    # Check for TPM/Program Manager  
    if "program manager" in title_lower or "tpm" in title_lower or "technical program" in title_lower:
        return {
            "role_family": "tpm_program", 
            "confidence": 90,
            "reason": "Program/TPM role"
        }
    
    # Check for Project Manager
    if "project manager" in title_lower:
        return {
            "role_family": "project",
            "confidence": 80,
            "reason": "Project Manager role"
        }
    
    # Default - other
    return {
        "role_family": "other",
        "confidence": 50,
        "reason": "No clear match"
    }
