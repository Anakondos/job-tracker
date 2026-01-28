"""
V7 Smart Form Filler - AI-Assisted Universal Form Filling

Key differences from V6:
1. Universal field scanning (not hardcoded selectors)
2. AI fallback when fill fails
3. Learns from corrections
4. Works on any ATS
"""

from playwright.sync_api import sync_playwright, Page, Frame, ElementHandle
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum
import anthropic
import json
import time
import os

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────

DATA_DIR = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/job-tracker-dev/data")
PROFILE_PATH = f"{DATA_DIR}/profile.json"
LEARNED_DB_PATH = f"{DATA_DIR}/learned_answers.json"

CV_PATH = "/Users/anton/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/CV_Anton_Kondakov_Product Manager.pdf"
COVER_LETTER_PATH = "/Users/anton/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/Cover_Letter_Anton_Kondakov_ProductM.docx"

# ─────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────

class FieldType(Enum):
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    DROPDOWN = "dropdown"
    SEARCHABLE_DROPDOWN = "searchable_dropdown"
    AUTOCOMPLETE = "autocomplete"
    CHECKBOX = "checkbox"
    FILE = "file"
    TEXTAREA = "textarea"

class AnswerSource(Enum):
    PROFILE = "profile"
    LEARNED = "learned"
    PATTERN = "pattern"
    AI = "ai"
    USER = "user"

@dataclass
class FormField:
    selector: str
    label: str
    field_type: FieldType
    element: Optional[ElementHandle] = None
    options: List[str] = field(default_factory=list)
    position_y: float = 0
    current_value: str = ""
    required: bool = False
    
@dataclass  
class Answer:
    value: str
    source: AnswerSource
    confidence: float = 1.0

# ─────────────────────────────────────────────────────────────────────
# LEARNED DATABASE
# ─────────────────────────────────────────────────────────────────────

class LearnedDB:
    """Database of learned answers and field methods"""
    
    def __init__(self, path: str):
        self.path = path
        self.data = {"answers": {}, "field_methods": {}, "ats_selectors": {}}
        self.load()
    
    def load(self):
        try:
            with open(self.path, 'r') as f:
                self.data = json.load(f)
        except:
            pass
    
    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get_answer(self, label: str) -> Optional[str]:
        """Get learned answer for label"""
        label_key = self._normalize_label(label)
        return self.data["answers"].get(label_key)
    
    def save_answer(self, label: str, answer: str, source: str = "auto"):
        """Save answer for future use"""
        label_key = self._normalize_label(label)
        self.data["answers"][label_key] = {
            "value": answer,
            "source": source,
            "times_used": self.data["answers"].get(label_key, {}).get("times_used", 0) + 1
        }
        self.save()
    
    def _normalize_label(self, label: str) -> str:
        """Normalize label for matching"""
        return label.lower().strip()[:100]

# ─────────────────────────────────────────────────────────────────────
# PROFILE
# ─────────────────────────────────────────────────────────────────────

class Profile:
    """User profile with field matching"""
    
    def __init__(self, path: str):
        self.data = {}
        self.load(path)
    
    def load(self, path: str):
        try:
            with open(path, 'r') as f:
                self.data = json.load(f)
        except:
            self.data = {}
    
    def match(self, label: str) -> Optional[str]:
        """Find profile value matching label"""
        label_lower = label.lower()
        
        # Direct mappings
        mappings = {
            "first name": self._get("personal.first_name"),
            "last name": self._get("personal.last_name"),
            "email": self._get("personal.email"),
            "phone": self._get("personal.phone"),
            "linkedin": self._get("personal.linkedin"),
            "location": self._get("personal.location", "Wake Forest"),
            "city": self._get("personal.city", "Wake Forest"),
            "country": "United States",
            "gender": self._get("demographics.gender", "Male"),
            "hispanic": self._get("demographics.hispanic_latino", "No"),
            "race": self._get("demographics.race", "White"),
            "veteran": self._get("demographics.veteran_status"),
            "disability": self._get("demographics.disability_status"),
        }
        
        for key, value in mappings.items():
            if key in label_lower and value:
                return value
        
        return None
    
    def _get(self, path: str, default: str = None) -> Optional[str]:
        """Get nested value from data"""
        keys = path.split(".")
        value = self.data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value else default
    
    def get_work_experience(self) -> List[Dict]:
        return self._get("employment") or []
    
    def get_education(self) -> List[Dict]:
        return self._get("education") or []
