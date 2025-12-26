"""
Profile manager for job application automation.

Handles:
- Loading/saving user profiles
- Smart field matching (fuzzy matching field names to profile data)
- Common answer library
"""

import json
from pathlib import Path
from typing import Optional, Dict, Any, List

PROFILE_DIR = Path(__file__).parent
DEFAULT_PROFILE_PATH = PROFILE_DIR / "profile.json"
TEMPLATE_PATH = PROFILE_DIR / "profile_template.json"


class ProfileManager:
    """Manages user profile data for form filling."""
    
    def __init__(self, profile_path: Optional[Path] = None):
        self.profile_path = profile_path or DEFAULT_PROFILE_PATH
        self.profile: Dict[str, Any] = {}
        self.field_mappings: Dict[str, List[str]] = {}
        self._load_profile()
    
    def _load_profile(self):
        """Load profile from JSON file."""
        if self.profile_path.exists():
            with open(self.profile_path) as f:
                self.profile = json.load(f)
        elif TEMPLATE_PATH.exists():
            # Load template if no profile exists
            with open(TEMPLATE_PATH) as f:
                self.profile = json.load(f)
        
        # Extract field mappings
        self.field_mappings = self.profile.get("field_mappings", {})
    
    def save_profile(self):
        """Save profile to JSON file."""
        with open(self.profile_path, "w") as f:
            json.dump(self.profile, f, indent=2, ensure_ascii=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get a profile value by key (supports nested keys like 'personal.email')."""
        keys = key.split(".")
        value = self.profile
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value
    
    def set(self, key: str, value: Any):
        """Set a profile value by key (supports nested keys)."""
        keys = key.split(".")
        target = self.profile
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value
    
    def get_value_for_field(self, field_name: str, field_id: str = "", 
                            placeholder: str = "", label: str = "") -> Optional[str]:
        """
        Smart matching: find the right profile value for a form field.
        
        Checks field name, id, placeholder, and label against known mappings.
        """
        # Combine all identifiers
        identifiers = [
            field_name.lower(),
            field_id.lower(),
            placeholder.lower(),
            label.lower(),
        ]
        
        # Check against each mapping
        for profile_key, patterns in self.field_mappings.items():
            if profile_key.startswith("_"):
                continue  # Skip comments
                
            for pattern in patterns:
                pattern_lower = pattern.lower()
                for identifier in identifiers:
                    if pattern_lower in identifier or identifier in pattern_lower:
                        # Found a match - get the value
                        value = self._get_profile_value(profile_key)
                        if value:
                            return value
        
        return None
    
    def _get_profile_value(self, key: str) -> Optional[str]:
        """Get value for a mapped field key."""
        # Direct mappings to profile sections
        mapping = {
            # Personal
            "first_name": "personal.first_name",
            "last_name": "personal.last_name",
            "full_name": "personal.full_name",
            "email": "personal.email",
            "phone": "personal.phone",
            "city": "personal.city",
            "state": "personal.state",
            "location": "personal.location",
            "zip_code": "personal.zip_code",
            "country": "personal.country",
            
            # Links
            "linkedin": "links.linkedin",
            "github": "links.github",
            "portfolio": "links.portfolio",
            
            # Work auth
            "authorized": "work_authorization.authorized_us",
            "sponsorship": "work_authorization.requires_sponsorship",
            
            # Demographics
            "gender": "demographics.gender",
            "veteran": "demographics.veteran_status",
            "disability": "demographics.disability_status",
            "ethnicity": "demographics.race_ethnicity",
            "hispanic": "demographics.hispanic_latino",
            
            # Availability
            "salary": "salary.expected_salary",
            "start_date": "availability.start_date",
            
            # Work experience (first entry)
            "company": "work_experience.0.company",
            "title": "work_experience.0.title",
            
            # Education (first entry)
            "school": "education.0.school",
            "degree": "education.0.degree",
            "discipline": "education.0.discipline",
        }
        
        profile_path = mapping.get(key)
        if profile_path:
            return self.get(profile_path)
        
        return None
    
    def get_common_answer(self, question_text: str) -> Optional[str]:
        """
        Find a pre-written answer for a common question.
        
        Uses fuzzy matching on question text.
        """
        question_lower = question_text.lower()
        answers = self.profile.get("common_answers", {})
        
        # Keywords to match
        keyword_map = {
            "why_interested": ["why interested", "why apply", "interest in this", "attracted to"],
            "why_company": ["why company", "why us", "why work here", "why join"],
            "strengths": ["strength", "what makes you", "best qualities"],
            "career_goals": ["career goal", "where do you see", "5 years", "future"],
            "management_style": ["management style", "leadership style", "how do you lead"],
            "challenge_overcome": ["challenge", "difficult situation", "obstacle", "problem you solved"],
            "how_heard": ["how did you hear", "where did you find", "how did you learn about"],
            "referral": ["referral", "referred by", "who referred"],
            "additional_info": ["additional", "anything else", "other information"],
        }
        
        for answer_key, keywords in keyword_map.items():
            for keyword in keywords:
                if keyword in question_lower:
                    answer = answers.get(answer_key)
                    if answer:
                        return answer
        
        return None
    
    def get_files(self) -> Dict[str, str]:
        """Get file paths for resume and cover letter."""
        return {
            "resume": self.get("files.resume_path", ""),
            "cover_letter": self.get("files.cover_letter_path", ""),
        }
    
    def get_work_experience(self) -> List[Dict]:
        """Get work experience entries."""
        return self.get("work_experience", [])
    
    def get_education(self) -> List[Dict]:
        """Get education entries."""
        return self.get("education", [])
    
    def is_complete(self) -> Dict[str, bool]:
        """Check which required fields are filled."""
        checks = {
            "name": bool(self.get("personal.first_name") and self.get("personal.last_name")),
            "email": bool(self.get("personal.email")),
            "phone": bool(self.get("personal.phone")),
            "location": bool(self.get("personal.location") or self.get("personal.city")),
            "linkedin": bool(self.get("links.linkedin")),
            "resume": bool(self.get("files.resume_path")),
            "work_experience": bool(self.get("work_experience.0.company")),
            "education": bool(self.get("education.0.school")),
        }
        return checks
    
    def print_status(self):
        """Print profile completion status."""
        checks = self.is_complete()
        print("\n📋 Profile Status:")
        for field, complete in checks.items():
            status = "✅" if complete else "❌"
            print(f"  {status} {field}")
        
        complete_count = sum(checks.values())
        total = len(checks)
        print(f"\n  Complete: {complete_count}/{total}")


# Singleton instance
_profile_manager: Optional[ProfileManager] = None

def get_profile_manager(profile_path: Optional[Path] = None) -> ProfileManager:
    """Get or create the profile manager singleton."""
    global _profile_manager
    if _profile_manager is None or profile_path:
        _profile_manager = ProfileManager(profile_path)
    return _profile_manager
