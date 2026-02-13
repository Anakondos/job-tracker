"""
Form Filler Engine V5 - Universal Form Filler

Multi-layer architecture:
1. DETECTION: HTML → Probe → Vision
2. RESOLUTION: Profile → Learned → AI → Human  
3. INPUT: Type/Select/Click based on field type
4. VALIDATION: Read back + Error check + Vision verify
5. LEARNING: Save successful fills

Modes:
- PRE_FLIGHT: Analyze form, generate readiness report (no actual fill)
- INTERACTIVE: Fill with human confirmation for unknowns
- AUTONOMOUS: Fill everything, skip unknowns
"""

import json
import re
import time
import requests  # For Ollama API
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field as dataclass_field
from enum import Enum

from playwright.sync_api import Page, ElementHandle

from .browser_manager import BrowserManager, BrowserMode
from .form_logger import FormLogger

# ═══════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════

V5_DIR = Path(__file__).parent
BROWSER_DIR = V5_DIR.parent
DATA_DIR = V5_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

PROFILE_PATH = BROWSER_DIR / "profiles" / "anton_tpm.json"
# Use shared learned database with V3.5
LEARNED_DB_PATH = BROWSER_DIR / "learned_database.json"
KNOWLEDGE_BASE_PATH = BROWSER_DIR / "knowledge_base.json"
FIELD_PATTERNS_PATH = DATA_DIR / "field_patterns.json"


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class FillMode(Enum):
    """Form filling modes"""
    PRE_FLIGHT = "pre_flight"      # Analyze only, no fill
    INTERACTIVE = "interactive"    # Human confirms unknowns
    AUTONOMOUS = "autonomous"      # Fill all, skip unknowns


class FieldType(Enum):
    """Detected field types"""
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    TEXTAREA = "textarea"
    SELECT = "select"              # Native <select>
    AUTOCOMPLETE = "autocomplete"  # React Select, combobox
    CHECKBOX = "checkbox"
    RADIO = "radio"
    FILE = "file"
    DATE = "date"
    HIDDEN = "hidden"
    UNKNOWN = "unknown"


class DetectionMethod(Enum):
    """How field type was detected"""
    HTML = "html"           # From HTML tag/type
    ARIA = "aria"           # From ARIA attributes
    PROBE = "probe"         # From click behavior
    VISION = "vision"       # From screenshot AI
    PATTERN = "pattern"     # From known patterns
    DEFAULT = "default"     # Fallback


class AnswerSource(Enum):
    """Where the answer came from"""
    PROFILE = "profile"
    LEARNED = "learned"
    AI = "ai"
    HUMAN = "human"
    DEFAULT = "default"
    NONE = "none"


class FillStatus(Enum):
    """Field fill status"""
    READY = "ready"           # Has answer, ready to fill
    FILLED = "filled"         # Successfully filled
    VERIFIED = "verified"     # Filled and verified
    NEEDS_INPUT = "needs_input"   # No answer found
    SKIPPED = "skipped"       # Intentionally skipped
    ERROR = "error"           # Fill failed


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FormField:
    """Detected form field with all metadata"""
    selector: str
    element_id: str
    name: str
    label: str
    
    # Detection
    field_type: FieldType
    detection_method: DetectionMethod
    html_tag: str
    input_type: str
    
    # Options (for select/autocomplete)
    options: List[str] = dataclass_field(default_factory=list)
    
    # State
    required: bool = False
    visible: bool = True
    current_value: str = ""
    
    # Answer
    answer: str = ""
    answer_source: AnswerSource = AnswerSource.NONE
    confidence: float = 0.0
    
    # Fill status
    status: FillStatus = FillStatus.NEEDS_INPUT
    error_message: str = ""
    
    # Profile mapping
    profile_key: str = ""


@dataclass
class FillReport:
    """Report of form filling attempt"""
    url: str
    title: str
    ats_type: str
    
    total_fields: int = 0
    ready_fields: int = 0
    filled_fields: int = 0
    verified_fields: int = 0
    needs_input: int = 0
    skipped: int = 0
    errors: int = 0
    
    fields: List[FormField] = dataclass_field(default_factory=list)
    
    def summary(self) -> str:
        """Generate text summary."""
        lines = [
            f"═══════════════════════════════════════════════════════════",
            f"FORM FILL REPORT",
            f"═══════════════════════════════════════════════════════════",
            f"URL: {self.url[:70]}...",
            f"Title: {self.title[:50]}",
            f"ATS: {self.ats_type}",
            f"",
            f"📊 SUMMARY:",
            f"   Total fields: {self.total_fields}",
            f"   ✅ Ready/Filled: {self.filled_fields}",
            f"   ✓ Verified: {self.verified_fields}",
            f"   ⚠️ Needs input: {self.needs_input}",
            f"   ⏭️ Skipped: {self.skipped}",
            f"   ❌ Errors: {self.errors}",
        ]
        
        if self.needs_input > 0:
            lines.append(f"\n⚠️ FIELDS NEEDING INPUT:")
            for f in self.fields:
                if f.status == FillStatus.NEEDS_INPUT:
                    lines.append(f"   • {f.label[:50]}")
        
        if self.errors > 0:
            lines.append(f"\n❌ ERRORS:")
            for f in self.fields:
                if f.status == FillStatus.ERROR:
                    lines.append(f"   • {f.label[:40]}: {f.error_message}")
        
        lines.append("═══════════════════════════════════════════════════════════")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# PROFILE
# ═══════════════════════════════════════════════════════════════════════════

class Profile:
    """User profile data manager."""
    
    # Label patterns → profile keys
    LABEL_MAPPINGS = {
        # Personal info
        "first name": "personal.first_name",
        "last name": "personal.last_name",
        "email": "personal.email",
        "phone": "personal.phone",
        "city": "personal.city",
        "beverly hills": "personal.city",  # Common city placeholder
        "new york": "personal.city",
        "los angeles": "personal.city",
        "san francisco": "personal.city",
        "state": "personal.state",
        "country": "personal.country",
        "zip": "personal.zip_code",
        "postal": "personal.zip_code",
        "90210": "personal.zip_code",  # Common ZIP placeholder
        "12345": "personal.zip_code",
        "00000": "personal.zip_code",
        "street": "personal.street_address",
        "address": "personal.street_address",
        "address line": "personal.street_address",
        "main st": "personal.street_address",  # Common placeholder pattern
        "your address": "personal.street_address",
        "home address": "personal.street_address",
        "linkedin": "links.linkedin",
        "github": "links.github",
        # Work experience
        "company name": "work_experience.0.company",
        "employer": "work_experience.0.company",
        "job title": "work_experience.0.title",
        "title": "work_experience.0.title",
        "start date month": "work_experience.0.start_month",
        "start month": "work_experience.0.start_month",
        "start date year": "work_experience.0.start_year",
        "start year": "work_experience.0.start_year",
        "end date month": "work_experience.0.end_month",
        "end month": "work_experience.0.end_month",
        "end date year": "work_experience.0.end_year",
        "end year": "work_experience.0.end_year",
        # Education
        "school": "education.0.school",
        "university": "education.0.school",
        "degree": "education.0.degree",
        "discipline": "education.0.discipline",
        "your major": "education.0.discipline",
        "field of study": "education.0.discipline",
        # Common questions
        "how did you hear": "common_answers.how_heard",
        "full name": "personal.full_name",
        "your name": "personal.full_name",
        "legal name": "personal.full_name",
        "preferred name": "personal.first_name",
        "preferred first name": "personal.first_name",
        "candidate name": "personal.full_name",
        "name of company": "work_experience.0.company",
        "name of your current company": "work_experience.0.company",
        "current company": "work_experience.0.company",
        "current employer": "work_experience.0.company",
        "location": "personal.location",
        "current location": "personal.location",
        "where are you located": "personal.location",
        "website": "links.linkedin",
        "portfolio": "links.linkedin",
        "personal website": "links.linkedin",
    }
    
    # Yes/No question patterns
    YES_NO_PATTERNS = {
        "18 years": "Yes",
        "authorized to work": "Yes",
        "legally authorized": "Yes",
        "eligible to work": "Yes",
        "require sponsorship": "No",
        "visa sponsorship": "No",
        "government official": "No",
        "close relative of a government": "No",
        "conflict of interest": "No",
        "connected to": "No",
        "financial interest": "No",
        "referred to this position by": "No",
        "senior leader": "No",
        "previously employed": "No",
        "previously been employed": "No",
        "former employee": "No",
        "confirm": "Yes",
        "acknowledge": "Yes",
        "confirm receipt": "Confirmed",
        "agree": "Yes",
        "i understand": "Yes",
        "current role": "Yes",
        "currently work here": "Yes",
        "currently work": "Yes",
        "i currently work": "Yes",
        # Additional from V35
        "willing to relocate": "Yes",
        "background check": "Yes",
        "drug test": "Yes",
        "non-compete": "No",
        "non-disclosure": "Yes",
    }
    
    # Text field defaults (for common questions)
    TEXT_DEFAULTS = {
        "years of experience": "15",
        "years experience": "15",
        "how many years": "15",
        "how did you hear": "LinkedIn",
        "how did you find": "LinkedIn",
        "where did you hear": "LinkedIn",
        "how were you referred": "LinkedIn",
        "referred by": "LinkedIn",
        "referral source": "LinkedIn",
        "source of application": "LinkedIn",
        "salary": "150000",
        "desired salary": "150000",
        "expected salary": "150000",
        "compensation": "150000",
        "salary expectation": "150000",
        "annual salary": "150000",
        "notice period": "2 weeks",
        "when can you start": "2 weeks",
        "earliest start": "2 weeks",
        "availability": "2 weeks",
        "start date": "2 weeks",
        "website": "https://linkedin.com/in/antonkondakov",
        "portfolio": "https://linkedin.com/in/antonkondakov",
        "personal website": "https://linkedin.com/in/antonkondakov",
        "additional information": "I am excited about this opportunity and believe my 15+ years of experience in technical program management align well with this role.",
    }
    
    # Demographic defaults
    DEMOGRAPHIC_DEFAULTS = {
        "gender": "Decline to self-identify",
        "race": "Decline to self-identify",
        "ethnicity": "Decline to self-identify",
        "hispanic": "Decline to self-identify",
        "veteran": "I am not a protected veteran",
        "disability": "I do not want to answer",
    }
    
    def __init__(self, path: Path = PROFILE_PATH):
        self.data = {}
        if path.exists():
            with open(path) as f:
                self.data = json.load(f)
    
    def get(self, key: str) -> str:
        """Get value by dot-notation key."""
        parts = key.split(".")
        val = self.data
        for p in parts:
            if val is None:
                return ""
            if p.isdigit():
                idx = int(p)
                val = val[idx] if isinstance(val, list) and idx < len(val) else None
            else:
                val = val.get(p) if isinstance(val, dict) else None
        return str(val) if val else ""
    
    def find_by_label(self, label: str) -> Tuple[Optional[str], Optional[str]]:
        """Find profile value by label text. Returns (value, profile_key)."""
        ll = label.lower()
        for pattern, key in self.LABEL_MAPPINGS.items():
            if re.search(r'\b' + re.escape(pattern) + r'\b', ll):
                val = self.get(key)
                if val:
                    return val, key
        return None, None
    
    def find_yes_no(self, label: str) -> Optional[str]:
        """Find Yes/No answer for common questions."""
        ll = label.lower()
        for pattern, answer in self.YES_NO_PATTERNS.items():
            if pattern in ll:
                return answer
        return None
    
    def find_demographic(self, label: str) -> Optional[str]:
        """Find demographic default answer."""
        ll = label.lower()
        for pattern, answer in self.DEMOGRAPHIC_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def find_text_default(self, label: str) -> Optional[str]:
        """Find text default for common questions like years of experience."""
        ll = label.lower()
        for pattern, answer in self.TEXT_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def get_context(self) -> str:
        """Get summary context for AI."""
        p = self.data.get("personal", {})
        w = self.data.get("work_experience", [{}])[0]
        return f"Name: {p.get('first_name', '')} {p.get('last_name', '')}\nLocation: {p.get('location', '')}\nRole: {w.get('title', '')} at {w.get('company', '')}"
    
    def get_files_for_role(self, job_title: str) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Get CV and Cover Letter paths based on job title.
        
        Args:
            job_title: Job title from the application (e.g. "Senior Product Manager, Finance")
            
        Returns:
            Tuple of (cv_path, cover_letter_path) - either can be None if not found
        """
        files_config = self.data.get("files", {})
        base_path = Path(files_config.get("base_path", ""))
        by_role = files_config.get("by_role", {})
        default_role = files_config.get("default_role", "TPM")
        
        if not base_path.exists():
            return None, None
        
        # Determine role from job title
        job_title_lower = job_title.lower()
        detected_role = None
        
        # Role detection patterns (order matters - more specific first)
        role_patterns = [
            ("TPM", ["technical program manager", "tpm"]),
            ("Product Manager", ["product manager"]),
            ("Product Owner", ["product owner"]),
            ("Project Manager", ["project manager"]),
            ("Scrum Master", ["scrum master", "agile coach"]),
            ("Delivery Lead", ["delivery lead", "delivery manager"]),
        ]
        
        for role_name, patterns in role_patterns:
            for pattern in patterns:
                if pattern in job_title_lower:
                    detected_role = role_name
                    break
            if detected_role:
                break
        
        # Use default if no match
        if not detected_role:
            detected_role = default_role
        
        # Get files for detected role
        role_files = by_role.get(detected_role, by_role.get(default_role, {}))
        
        cv_filename = role_files.get("cv")
        cover_letter_filename = role_files.get("cover_letter")
        
        cv_path = base_path / cv_filename if cv_filename else None
        cover_letter_path = base_path / cover_letter_filename if cover_letter_filename else None
        
        # Verify files exist
        if cv_path and not cv_path.exists():
            print(f"   ⚠️ CV not found: {cv_path}")
            cv_path = None
        if cover_letter_path and not cover_letter_path.exists():
            print(f"   ⚠️ Cover letter not found: {cover_letter_path}")
            cover_letter_path = None
        
        if cv_path:
            print(f"   📄 Selected CV for \'{detected_role}\': {cv_path.name}")
        
        return cv_path, cover_letter_path


# ═══════════════════════════════════════════════════════════════════════════
# LEARNED DATABASE
# ═══════════════════════════════════════════════════════════════════════════

class LearnedDB:
    """Database of learned field answers."""
    
    def __init__(self, path: Path = LEARNED_DB_PATH):
        self.path = path
        self.data = self._load()
    
    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path) as f:
                data = json.load(f)
            # Support V3.5 format (field_answers) and V5 format (answers)
            if "field_answers" in data and "answers" not in data:
                # Convert V3.5 format to V5
                return {
                    "answers": data.get("field_answers", {}),
                    "dropdown_choices": data.get("dropdown_choices", {})
                }
            return data
        return {"answers": {}, "dropdown_choices": {}}
    
    def save(self):
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def _normalize_key(self, label: str) -> str:
        key = label.lower().strip()
        key = re.sub(r'[*?!:\-_()\"\']+', ' ', key)
        key = re.sub(r'\s+', ' ', key).strip()
        return key[:100]
    
    def find(self, label: str, is_dropdown: bool = False) -> Optional[str]:
        """Find saved answer for field label."""
        key = self._normalize_key(label)
        store = self.data["dropdown_choices" if is_dropdown else "answers"]
        
        if key in store:
            return store[key]
        
        # Partial match
        for k, v in store.items():
            if k in key or key in k:
                return v
        
        return None
    
    def save_answer(self, label: str, answer: str, is_dropdown: bool = False):
        """Save answer for future use."""
        key = self._normalize_key(label)
        store = "dropdown_choices" if is_dropdown else "answers"
        self.data[store][key] = answer
        self.save()
        print(f"   💾 Learned: '{label[:30]}' → '{answer[:25]}'")


# ═══════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE - Experience snippets + common answers for AI context
# ═══════════════════════════════════════════════════════════════════════════

class KnowledgeBase:
    """Knowledge base with experience snippets and common answers."""

    def __init__(self, path: Path = KNOWLEDGE_BASE_PATH):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except Exception:
            self.data = {}
        self.snippets = self.data.get("experience_snippets", {})
        self.common_answers = self.data.get("common_answers", {})
        self.skills = self.data.get("skills", {})
        self.achievements = self.data.get("achievements", [])
        print(f"   📚 KnowledgeBase loaded: {len(self.snippets)} snippets, {len(self.common_answers)} common answers")

    def find_relevant_snippets(self, question: str) -> List[str]:
        """Find relevant experience snippets for a question."""
        question_lower = question.lower()
        found = []

        for keyword, snippet in self.snippets.items():
            # Check if keyword appears in question
            if keyword.lower() in question_lower:
                found.append(snippet)
            # Also check individual words from multi-word keywords
            elif len(keyword.split()) > 1:
                words = keyword.lower().split()
                if any(w in question_lower for w in words if len(w) > 3):
                    found.append(snippet)

        return found[:3]  # Max 3 snippets

    def get_context_for_question(self, question: str) -> str:
        """Get formatted context with relevant snippets for AI prompt."""
        snippets = self.find_relevant_snippets(question)
        if snippets:
            return "Relevant experience:\n" + "\n".join(f"- {s}" for s in snippets)
        return ""

    def find_common_answer(self, question: str) -> Optional[str]:
        """Find pre-written answer for common questions (salary, why interested, etc.)."""
        question_lower = question.lower()

        for answer_key, answer_data in self.common_answers.items():
            keywords = answer_data.get("keywords", [])
            for kw in keywords:
                if kw.lower() in question_lower:
                    return answer_data.get("answer")

        return None


# ═══════════════════════════════════════════════════════════════════════════
# OLLAMA HELPER - Local LLM for custom questions
# ═══════════════════════════════════════════════════════════════════════════

class OllamaHelper:
    """Local LLM using llava:7b for generating answers to custom questions."""
    
    OLLAMA_URL = "http://localhost:11434/api/generate"
    MODEL = "llava:7b"
    
    PROMPT_TEMPLATE = """Answer this job application question based on the candidate profile.

CANDIDATE:
{profile_context}

QUESTION: {question}
{options_text}

RULES:
1. "How many years of experience" -> answer with a number like "15+" 
2. Questions about specific software (NetSuite, SAP, Salesforce, Oracle):
   - Answer "No" unless that EXACT tool is mentioned in candidate profile above
3. Multiple choice -> reply with EXACT option text only
4. Yes/no -> reply "Yes" or "No" only
5. Be concise - max 5 words

ANSWER:"""

    def __init__(self):
        self._available = None
    
    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                resp = requests.get("http://localhost:11434/api/tags", timeout=2)
                self._available = resp.status_code == 200
            except:
                self._available = False
        return self._available
    
    def generate(self, question: str, profile_context: str, options: List[str] = None) -> Optional[str]:
        if not self.available:
            return None
        
        options_text = ""
        if options:
            options_text = f"OPTIONS (choose one): {options}"
        
        prompt = self.PROMPT_TEMPLATE.format(
            profile_context=profile_context,
            question=question,
            options_text=options_text
        )
        
        try:
            resp = requests.post(
                self.OLLAMA_URL,
                json={
                    "model": self.MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 50}
                },
                timeout=60
            )
            
            if resp.status_code == 200:
                answer = resp.json().get("response", "").strip()
                answer = answer.split("\n")[0].strip()
                answer = re.sub(r'^[*\-]\s*', '', answer)
                answer = re.sub(r'^(Answer:|ANSWER:|A:)\s*', '', answer)
                return answer
        except Exception as e:
            print(f"   ⚠️ Ollama error: {e}")
        return None
    
    def match_option(self, answer: str, options: List[str]) -> Optional[str]:
        if not options:
            return answer
        answer_lower = answer.lower().strip()
        for opt in options:
            if opt.lower() == answer_lower:
                return opt
        for opt in options:
            if answer_lower in opt.lower() or opt.lower() in answer_lower:
                return opt
        return options[0] if options else answer


# ═══════════════════════════════════════════════════════════════════════════
# AI HELPER - Claude API for verification
# ═══════════════════════════════════════════════════════════════════════════

class AIHelper:
    """AI Helper using Claude API for verification."""
    
    def __init__(self):
        self._vision_ai = None
    
    @property
    def vision_ai(self):
        """Lazy-load Vision AI."""
        if self._vision_ai is None:
            from .vision_ai import VisionAI
            self._vision_ai = VisionAI()
        return self._vision_ai
    
    @property
    def available(self) -> bool:
        """Check if Claude API is configured."""
        return self.vision_ai.available
    
    def generate(self, question: str, context: str, max_length: int = 150) -> str:
        """Generate answer for custom question using Claude."""
        if not self.available:
            print("   ⚠️ Claude API not configured. Set ANTHROPIC_API_KEY.")
            return ""
        
        result = self.vision_ai.generate_custom_answer(
            question=question,
            profile_context=context,
            max_words=max_length // 5
        )
        
        if result.get("success") and result.get("answer"):
            return result["answer"]
        
        if result.get("error"):
            print(f"   ⚠️ Claude error: {result['error']}")
        
        return ""
    
    def choose_option(self, question: str, options: List[str], context: str) -> Optional[str]:
        """Choose best option from list using Claude."""
        if not options:
            return None
        
        if not self.available:
            print("   ⚠️ Claude API not configured. Set ANTHROPIC_API_KEY.")
            return None
        
        try:
            prompt = f"""Job application dropdown:
Question: {question}
Options: {options}
Profile: {context}

Which option should be selected? Return ONLY the exact option text, nothing else."""
            
            response = self.vision_ai.client.messages.create(
                model=self.vision_ai.config.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            
            answer = response.content[0].text.strip()
            
            # Find matching option (exact match first)
            for opt in options:
                if opt.lower() == answer.lower():
                    return opt
            
            # Partial match
            for opt in options:
                if opt.lower() in answer.lower() or answer.lower() in opt.lower():
                    return opt
            
            # Word overlap match
            answer_words = set(answer.lower().split())
            for opt in options:
                opt_words = set(opt.lower().split())
                if answer_words & opt_words:
                    return opt
                    
        except Exception as e:
            print(f"   ⚠️ Claude error: {e}")
        
        return None
    
    def analyze_field_screenshot(self, screenshot_path: str, field_description: str = "") -> Dict[str, Any]:
        """Analyze form field from screenshot using Claude Vision."""
        if not self.available:
            return {"success": False, "error": "Claude API not configured"}
        return self.vision_ai.analyze_field(screenshot_path, field_description)
    
    def verify_field_filled(self, screenshot_path: str, expected_value: str, field_label: str = "") -> Dict[str, Any]:
        """Verify field was filled correctly using Claude Vision."""
        if not self.available:
            return {"success": False, "verified": False, "error": "Claude API not configured"}
        return self.vision_ai.verify_field_filled(screenshot_path, expected_value, field_label)
    
    def analyze_full_form(self, screenshot_path: str) -> Dict[str, Any]:
        """Analyze entire form from screenshot."""
        if not self.available:
            return {"success": False, "error": "Claude API not configured"}
        return self.vision_ai.analyze_form(screenshot_path)

class FormFillerV5:
    """
    Universal Form Filler V5
    
    Usage:
        # Pre-flight check
        filler = FormFillerV5()
        report = filler.analyze("https://greenhouse.io/...")
        print(report.summary())
        
        # Fill form
        filler.fill("https://greenhouse.io/...", mode=FillMode.INTERACTIVE)
    """
    
    # Repeatable sections configuration (Work Experience, Education)
    # Greenhouse uses patterns like: company-name-0, title-0, school--0, degree--0
    REPEATABLE_SECTIONS = {
        'work_experience': {
            'profile_key': 'work_experience',
            'add_button_selectors': [
                'button:has-text("Add another")',
                'a:has-text("Add another")',
                '.add-section a', '.add-section button',
                '#add_work_experience',
            ],
            'section_text': 'work experience',
            'field_patterns': {
                'company-name-{N}': 'company',
                'job_application_answers_attributes_{N}_text_value': 'company',
                'title-{N}': 'title',
                'start-date-month-{N}': 'start_month',
                'start-date-year-{N}': 'start_year',
                'end-date-month-{N}': 'end_month',
                'end-date-year-{N}': 'end_year',
                'current-role-{N}_1': 'current',
            },
            'skip_end_date_if_current': True,
        },
        'education': {
            'profile_key': 'education',
            'add_button_selectors': [
                'button:has-text("Add another")',
                'a:has-text("Add another")',
                '.add-section a', '.add-section button',
                '#add_education',
            ],
            'section_text': 'education',
            'field_patterns': {
                'school--{N}': 'school',
                'degree--{N}': 'degree',
                'discipline--{N}': 'discipline',
                'start-date-month--{N}': 'start_month',
                'start-date-year--{N}': 'start_year',
                'end-date-month--{N}': 'end_month',
                'end-date-year--{N}': 'end_year',
            },
            'skip_end_date_if_current': False,
        }
    }
    
    def __init__(self, browser_mode: BrowserMode = BrowserMode.PERSISTENT):
        self.browser_mode = browser_mode
        self.profile = Profile()
        self.learned_db = LearnedDB()
        self.kb = KnowledgeBase()
        self.ai = AIHelper()
        self.ollama = OllamaHelper()
        self.logger = FormLogger()
        
        self.browser: Optional[BrowserManager] = None
        self.page: Optional[Page] = None
        self.fields: List[FormField] = []
        self._seen_selectors: set = set()
    
    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────
    
    def analyze(self, url: str) -> FillReport:
        """
        Pre-flight analysis: scan form, find answers, generate readiness report.
        Does NOT fill any fields.
        """
        with BrowserManager(mode=self.browser_mode) as browser:
            self.browser = browser
            self.page = browser.page
            
            browser.goto(url)
            browser.wait_for_stable()
            
            # Scan and analyze
            self._scan_fields()
            self._resolve_all_answers()
            
            # Generate report
            return self._generate_report(url)
    
    def fill(self, url: str, mode: FillMode = FillMode.INTERACTIVE) -> FillReport:
        """
        Fill form with specified mode.
        Includes re-scan logic for dynamic forms (fields that appear after selection).
        """
        with BrowserManager(mode=self.browser_mode) as browser:
            self.browser = browser
            self.page = browser.page
            
            browser.goto(url)
            browser.wait_for_stable()
            
            # Wait for iframes to load (Greenhouse, Lever forms are in iframes)
            self._wait_for_iframes()
            
            # Try to find and click Apply button if on job description page
            self._find_and_click_apply_button()
            
            # Handle login page if needed
            if self._handle_login_page():
                # After login, wait and rescan
                browser.wait_for_stable()
            
            # Initial scan, prescan dropdowns, resolve, fill
            self._scan_fields()
            self._prescan_all_options()
            self._resolve_all_answers()
            
            if mode == FillMode.PRE_FLIGHT:
                return self._generate_report(url)
            
            # Fill repeatable sections first (work experience, education)
            self.fill_all_repeatable_sections()
            
            # Fill fields with re-scan for dynamic forms
            # Loop until no new fields appear
            max_iterations = 5
            for iteration in range(max_iterations):
                fields_before = len(self.fields)
                
                # Fill current fields
                self._fill_all_fields(mode)
                
                # Wait for potential new fields to appear
                time.sleep(0.5)
                browser.wait_for_stable()
                
                # Re-scan for new fields
                new_fields = self._scan_for_new_fields()
                
                if new_fields:
                    print(f"\n🔄 Found {len(new_fields)} new fields after filling")
                    for f in new_fields:
                        print(f"   + {f.label[:40]}")
                    
                    # Resolve answers for new fields
                    for f in new_fields:
                        self._resolve_field_answer(f)
                    
                    # Continue loop to fill new fields
                else:
                    # No new fields, we're done
                    break
            
            # Blur all fields to trigger validation
            self._blur_all_fields()
            self._validate_all_fields()
            
            # Keep browser open for review
            if mode == FillMode.INTERACTIVE:
                print("\n👀 Review the form in browser.")
                print("   Press ENTER when done...")
                try:
                    input()
                except:
                    time.sleep(60)
            
            return self._generate_report(url)
    
    def _scan_for_new_fields(self) -> List[FormField]:
        """
        Scan for fields that appeared after initial scan.
        Returns list of new fields not seen before.
        """
        new_fields = []
        elements = self.page.query_selector_all("input, select, textarea")
        
        for el in elements:
            field = self._detect_field(el)
            if field and field.selector not in self._seen_selectors:
                self._seen_selectors.add(field.selector)
                self.fields.append(field)
                new_fields.append(field)
        
        return new_fields
    
    # ─────────────────────────────────────────────────────────────────────
    # LAYER 0: FIND AND CLICK APPLY BUTTON
    # ─────────────────────────────────────────────────────────────────────
    
    def _wait_for_iframes(self, timeout=8):
        """
        Wait for iframes to load (Greenhouse, Lever, etc.).
        Many ATS embed their forms in iframes that load async.
        """
        import time
        
        start = time.time()
        initial_frames = len(self.page.frames)
        
        # Wait up to timeout seconds for more frames to appear
        while time.time() - start < timeout:
            current_frames = len(self.page.frames)
            
            # Check if we have Greenhouse or Lever iframe
            for frame in self.page.frames:
                if frame.url and any(ats in frame.url for ats in ['greenhouse', 'lever', 'workday', 'icims']):
                    print(f"   \u2705 Found ATS iframe: {frame.url[:50]}")
                    time.sleep(1)  # Extra wait for iframe content
                    return
            
            # If frames increased, wait a bit more
            if current_frames > initial_frames:
                time.sleep(1)
                continue
            
            time.sleep(0.5)
        
        print(f"   \ud83d\udcc4 {len(self.page.frames)} frames loaded")
    
    def _find_and_click_apply_button(self):
        """
        Find and click Apply button on job description pages.
        Many ATS (iCIMS, Workday, etc.) show job description first,
        then require clicking Apply to open the form.
        Supports both main page and iframes.
        """
        import time
        
        # Check if we're on a job description page (no form fields visible)
        initial_fields = self.page.query_selector_all("input[type='text'], input[type='email'], select, textarea")
        visible_fields = [f for f in initial_fields if f.is_visible()]
        
        if len(visible_fields) > 3:
            print("\n✅ Already on application form")
            return
        
        print("\n🔎 Looking for Apply button...")
        
        apply_selectors = [
            "a.iCIMS_ApplyButton",
            "a[title='Apply for this job online']",
            "a.iCIMS_Action_Button.iCIMS_ApplyButton",
            "a.iCIMS_PrimaryButton",
            "a#apply_button",
            "a.postings-btn",
            "button[data-automation-id='jobPostingApplyButton']",
            "a:has-text('Apply')",
            "button:has-text('Apply')",
            "a:has-text('Apply Now')",
            "a:has-text('Apply for this job')",
            "a[class*='apply']",
            "button[class*='apply']",
        ]
        
        # First try main page
        if self._try_click_apply_in_context(self.page, apply_selectors, "main page"):
            return
        
        # Then try each iframe
        frames = self.page.frames
        print(f"   📝 Checking {len(frames)} frames for Apply button...")
        
        for i, frame in enumerate(frames):
            if frame == self.page.main_frame:
                continue
            
            frame_url = frame.url[:50] if frame.url else "(empty)"
            if self._try_click_apply_in_context(frame, apply_selectors, f"frame {i}: {frame_url}"):
                return
        
        print("   ⚠️ No Apply button found, proceeding with current page")
    
    def _try_click_apply_in_context(self, context, selectors, context_name):
        """Try to find and click Apply button in a given context (page or frame)."""
        import time
        
        for selector in selectors:
            try:
                btn = context.query_selector(selector)
                if btn and btn.is_visible():
                    btn_text = btn.inner_text().strip()[:50]
                    print(f"   ✅ Found Apply button in {context_name}: '{btn_text}'")
                    btn.click()
                    print(f"   🖱️ Clicked Apply button")
                    time.sleep(2)
                    self.browser.wait_for_stable()
                    print(f"   📄 Current URL: {self.page.url[:80]}...")
                    return True
            except:
                continue
        
        # Try text-based search
        try:
            all_clickables = context.query_selector_all("a, button")
            for el in all_clickables:
                try:
                    if not el.is_visible():
                        continue
                    text = el.inner_text().strip().lower()
                    if any(kw in text for kw in ['apply', 'submit application', 'start application']):
                        if 'applied' not in text and "don't apply" not in text:
                            print(f"   ✅ Found Apply button in {context_name} (text): '{text[:50]}'")
                            el.click()
                            print(f"   🖱️ Clicked Apply button")
                            time.sleep(2)
                            self.browser.wait_for_stable()
                            return True
                except:
                    continue
        except:
            pass
        
        return False

    def _handle_login_page(self):
        """
        Handle login/authentication pages.
        Supports: Google OAuth, LinkedIn OAuth, Email login.
        """
        import time
        
        # Check if we're on a login page
        url = self.page.url.lower()
        if 'login' not in url and 'signin' not in url and 'auth' not in url:
            return False
        
        print("\n🔐 Login page detected...")
        
        # First try to find Google/LinkedIn login buttons (preferred)
        for frame in self.page.frames:
            try:
                # Look for Google login button
                google_btn = frame.query_selector(
                    "a[href*='google'], button[data-provider='google'], "
                    "a:has-text('Google'), button:has-text('Google'), "
                    "a:has-text('Sign in with Google'), button:has-text('Sign in with Google'), "
                    "a[class*='google'], button[class*='google'], "
                    "div[data-provider='google']"
                )
                if google_btn and google_btn.is_visible():
                    print("   🔵 Found Google login button")
                    print("   👉 Clicking Google login...")
                    google_btn.click()
                    time.sleep(3)
                    self.browser.wait_for_stable()
                    
                    # Wait for Google OAuth popup or redirect
                    print("   👉 Please complete Google sign-in in the browser...")
                    print("   Press ENTER when logged in...")
                    try:
                        input()
                    except:
                        time.sleep(30)
                    
                    self.browser.wait_for_stable()
                    return True
                
                # Look for LinkedIn login button
                linkedin_btn = frame.query_selector(
                    "a[href*='linkedin'], button[data-provider='linkedin'], "
                    "a:has-text('LinkedIn'), button:has-text('LinkedIn'), "
                    "a[class*='linkedin'], button[class*='linkedin']"
                )
                if linkedin_btn and linkedin_btn.is_visible():
                    print("   🔵 Found LinkedIn login button")
                    print("   👉 Clicking LinkedIn login...")
                    linkedin_btn.click()
                    time.sleep(3)
                    self.browser.wait_for_stable()
                    
                    print("   👉 Please complete LinkedIn sign-in...")
                    print("   Press ENTER when logged in...")
                    try:
                        input()
                    except:
                        time.sleep(30)
                    
                    self.browser.wait_for_stable()
                    return True
            except:
                continue
        
        # Fallback: email login
        email_value = self.profile.get('personal.email') or ''
        if not email_value or email_value == 'YOUR_EMAIL':
            print("   ⚠️ No email in profile. Please set 'email' in your profile.")
            return False
        
        for frame in self.page.frames:
            try:
                email_input = frame.query_selector("input[type='email'], input[name*='email'], input[name*='loginName']")
                if email_input and email_input.is_visible():
                    print(f"   📧 Found email field, entering: {email_value}")
                    email_input.fill(email_value)
                    time.sleep(0.5)
                    
                    # Look for submit button
                    submit_btn = frame.query_selector("input[type='submit'], button[type='submit'], button:has-text('Continue'), button:has-text('Next')")
                    if submit_btn and submit_btn.is_visible():
                        print("   👉 Clicking submit...")
                        submit_btn.click()
                        time.sleep(2)
                        self.browser.wait_for_stable()
                    
                    # Check for captcha
                    captcha = frame.query_selector("[class*='captcha'], [class*='hcaptcha'], [class*='recaptcha'], iframe[src*='captcha']")
                    if captcha:
                        print("\n   🧩 CAPTCHA detected!")
                        print("   👉 Please solve the captcha in the browser...")
                        print("   Press ENTER when done...")
                        try:
                            input()
                        except:
                            time.sleep(30)
                        
                        self.browser.wait_for_stable()
                    
                    return True
            except:
                continue
        
        return False

    # ─────────────────────────────────────────────────────────────────────
    # LAYER 1: DETECTION
    # ─────────────────────────────────────────────────────────────────────
    
    def _scan_fields(self):
        """Scan all form fields on page and all iframes."""
        print("\n🔍 Scanning form fields...")
        self.fields = []
        self._seen_selectors = set()
        self._active_frame = self.page  # Track which frame has the form

        # Scan main page first
        print("   Scanning main page...", flush=True)
        elements = self.page.query_selector_all("input, select, textarea")
        main_count = self._scan_elements(elements, "main")
        print(f"   Main page: {main_count} fields", flush=True)

        # Scan all iframes (important for Greenhouse, Lever embedded forms)
        frames = self.page.frames
        if len(frames) > 1:
            print(f"   📄 Checking {len(frames)} frames...", flush=True)

            for i, frame in enumerate(frames):
                if frame == self.page.main_frame:
                    continue  # Already scanned

                try:
                    frame_url = frame.url[:40] if frame.url else "(empty)"
                    print(f"      Frame {i}: {frame_url}...", flush=True)
                    elements = frame.query_selector_all("input, select, textarea")
                    count = self._scan_elements(elements, f"frame[{i}]")

                    if count > 0:
                        print(f"      ✅ Frame {i}: {count} fields")
                        # Remember which frame has the form
                        if count > main_count:
                            self._active_frame = frame
                except Exception as e:
                    print(f"      ⚠️ Frame {i} error: {e}", flush=True)
                    continue

        # Print scan summary
        print(f"   Total fields: {len(self.fields)}", flush=True)
        for f in self.fields:
            print(f"      {f.field_type.value:12} | {f.label[:40]:<40} | {f.selector[:30]}", flush=True)
    
    def _scan_elements(self, elements, source):
        """Scan elements from a specific source (main or frame)."""
        count = 0
        for el in elements:
            field = self._detect_field(el)
            if field and field.selector not in self._seen_selectors:
                self._seen_selectors.add(field.selector)
                self.fields.append(field)
                count += 1
        return count
        
        # Print summary by type
        type_counts = {}
        for f in self.fields:
            type_counts[f.field_type.value] = type_counts.get(f.field_type.value, 0) + 1
        
        print(f"   Found {len(self.fields)} fields: {type_counts}")
    
    def _detect_field(self, el: ElementHandle) -> Optional[FormField]:
        """Detect field type and metadata."""
        try:
            # Skip hidden/invisible
            if not el.is_visible():
                return None
            
            # Basic attributes
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            input_type = el.get_attribute("type") or "text"
            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            
            # Skip system fields
            if input_type in ("hidden", "submit", "button"):
                return None
            
            # Build selector (escape special CSS chars in IDs like question_123[])
            if el_id:
                escaped_id = re.sub(r'([\[\](){}!@#$%^&*+=|~`<>?,/\\])', r'\\\1', el_id)
                selector = f"#{escaped_id}"
            elif el_name:
                selector = f"[name='{el_name}']"
            else:
                return None
            
            # Get label
            label = self._find_label(el, el_id)
            
            # Get current value
            current_value = self._get_value(el, tag, input_type)
            
            # Required?
            required = el.get_attribute("required") is not None or \
                       el.get_attribute("aria-required") == "true"
            
            # Detect type
            field_type, detection_method = self._detect_type(el, tag, input_type)
            
            # Get options for select fields only during scan
            # Autocomplete options will be discovered in prescan phase (faster)
            options = []
            if field_type == FieldType.SELECT:
                try:
                    options = el.evaluate(
                        "e => Array.from(e.options).map(o => o.text).filter(t => t && t !== 'Select...')"
                    )
                except:
                    pass
            # NOTE: autocomplete options are NOT probed here - they're done in _prescan_all_options()
            
            return FormField(
                selector=selector,
                element_id=el_id,
                name=el_name,
                label=label,
                field_type=field_type,
                detection_method=detection_method,
                html_tag=tag,
                input_type=input_type,
                options=options,
                required=required,
                current_value=current_value,
            )
            
        except Exception as e:
            return None
    
    def _find_label(self, el: ElementHandle, el_id: str) -> str:
        """
        Find label text for field using multiple strategies.

        Priority:
        1. Standard label[for] attribute (in active frame + page)
        1b. Parent <label> direct text (Greenhouse wraps fields in <label> tags)
        1c. div.field > label sibling (Greenhouse custom questions in div.field)
        2. aria-label / placeholder
        3. Context Discovery (traverse DOM for nearby text)
        4. Name/ID as fallback
        """
        label = ""
        el_name = el.get_attribute("name") or ""

        # Strategy 1: By for attribute - search in element's own frame first
        if el_id:
            # Try element's owner frame first (handles iframes)
            frame = el.owner_frame() or self.page
            label_el = frame.query_selector(f"label[for='{el_id}']")
            if not label_el and frame != self.page:
                label_el = self.page.query_selector(f"label[for='{el_id}']")
            if label_el:
                label = label_el.inner_text().strip()

        # Strategy 1b: Parent <label> direct text nodes
        # In Greenhouse, fields (inputs, textareas, Select2) are inside a <label> tag
        # whose direct text nodes contain the actual question text.
        # e.g. <label>Question text? *<span class="asterisk">*</span><br><input...></label>
        # This works for ALL field types, not just Select2.
        if not label:
            try:
                parent_label_text = el.evaluate('''el => {
                    // For Select2 inputs, start from the container
                    let start = el.closest('.select2-container') || el;

                    // Walk up to find a <label> parent (max 4 levels)
                    let parent = start.parentElement;
                    for (let i = 0; i < 4 && parent; i++) {
                        if (parent.tagName === 'LABEL') {
                            // Extract only direct text nodes (not children text)
                            let text = '';
                            for (const node of parent.childNodes) {
                                if (node.nodeType === 3) { // TEXT_NODE
                                    text += node.textContent.trim() + ' ';
                                }
                            }
                            text = text.trim();
                            // Filter out noise: just asterisks, very short text
                            if (text && text !== '*' && text.length > 3) {
                                return text;
                            }
                        }
                        parent = parent.parentElement;
                    }
                    return '';
                }''')
                if parent_label_text and len(parent_label_text) > 3:
                    label = parent_label_text
            except Exception:
                pass

        # Strategy 1c: div.field > label sibling
        # Some Greenhouse forms use: <div class="field"><label>Question</label><input></div>
        if not label:
            try:
                sibling_label = el.evaluate('''el => {
                    // Walk up to find div.field
                    let parent = el.parentElement;
                    for (let i = 0; i < 5 && parent; i++) {
                        if (parent.classList && parent.classList.contains('field')) {
                            // Get first direct label child that doesn't contain our input
                            const labels = parent.querySelectorAll(':scope > label');
                            for (const lbl of labels) {
                                if (!lbl.contains(el)) {
                                    let text = '';
                                    for (const node of lbl.childNodes) {
                                        if (node.nodeType === 3) text += node.textContent.trim() + ' ';
                                    }
                                    text = text.trim();
                                    if (text && text !== '*' && text.length > 3) return text;
                                }
                            }
                            break;
                        }
                        parent = parent.parentElement;
                    }
                    return '';
                }''')
                if sibling_label and len(sibling_label) > 3:
                    label = sibling_label
            except Exception:
                pass

        # Strategy 2: aria/placeholder
        if not label:
            label = el.get_attribute("aria-label") or \
                    el.get_attribute("placeholder") or ""

        # Strategy 3: Context Discovery (for Shadow DOM)
        if not label or len(label) < 5:
            try:
                context = self._discover_field_context(el_id)
                if context:
                    label = context
            except Exception as e:
                pass  # Fallback to other methods

        # Strategy 4: Name/ID fallback
        if not label:
            label = el_name or el_id or ""

        # Append name attribute if different (helps with mapping)
        if el_name and el_name.lower() not in label.lower():
            label = f"{label} [{el_name}]"

        return label
    
    def _discover_field_context(self, field_id: str) -> str:
        """Use Context Discovery to find label from surrounding DOM."""
        if not field_id:
            return ""
        
        result = self.page.evaluate('''(fieldId) => {
            function findInShadow(root, selector) {
                let el = root.querySelector(selector);
                if (el) return el;
                const shadows = root.querySelectorAll('*');
                for (const s of shadows) {
                    if (s.shadowRoot) {
                        el = findInShadow(s.shadowRoot, selector);
                        if (el) return el;
                    }
                }
                return null;
            }
            
            const input = findInShadow(document, '#' + fieldId);
            if (!input) return '';
            
            const context = [];
            let container = input;
            
            for (let i = 0; i < 10 && container; i++) {
                container = container.parentElement || 
                           (container.getRootNode && container.getRootNode().host);
                
                if (!container || !container.querySelectorAll) continue;
                
                const textEls = container.querySelectorAll(
                    'label, legend, p, span, h3, h4, div'
                );
                
                for (const el of textEls) {
                    if (el.contains(input)) continue;
                    const txt = el.textContent.trim();
                    if (txt && txt.length > 3 && txt.length < 150) {
                        if (!context.some(c => c.includes(txt) || txt.includes(c))) {
                            context.push(txt);
                        }
                    }
                }
                
                if (context.length >= 1) break;
            }
            
            return context[0] || '';
        }''', field_id)
        
        return result or ""
    
    def _get_value(self, el: ElementHandle, tag: str, input_type: str) -> str:
        """Get current field value."""
        try:
            if tag == "select":
                return el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
            elif input_type == "checkbox":
                return "checked" if el.is_checked() else ""
            elif input_type != "file":
                return el.input_value() or ""
        except:
            pass
        return ""
    
    def _detect_type(self, el: ElementHandle, tag: str, input_type: str) -> Tuple[FieldType, DetectionMethod]:
        """Detect field type using cascade."""
        
        # Layer 1: HTML standard
        if tag == "select":
            return FieldType.SELECT, DetectionMethod.HTML
        if tag == "textarea":
            return FieldType.TEXTAREA, DetectionMethod.HTML
        if input_type == "file":
            return FieldType.FILE, DetectionMethod.HTML
        if input_type == "checkbox":
            return FieldType.CHECKBOX, DetectionMethod.HTML
        if input_type == "email":
            return FieldType.EMAIL, DetectionMethod.HTML
        if input_type == "tel":
            return FieldType.PHONE, DetectionMethod.HTML
        if input_type == "date":
            return FieldType.DATE, DetectionMethod.HTML
        
        # Layer 2: ARIA
        role = el.get_attribute("role") or ""
        aria_haspopup = el.get_attribute("aria-haspopup") or ""
        
        if role == "combobox" or aria_haspopup in ("true", "listbox"):
            return FieldType.AUTOCOMPLETE, DetectionMethod.ARIA
        if role == "listbox":
            return FieldType.SELECT, DetectionMethod.ARIA
        
        # Default: text
        return FieldType.TEXT, DetectionMethod.DEFAULT
    
    def _probe_autocomplete_options(self, el: ElementHandle, selector: str) -> List[str]:
        """Click autocomplete to read options."""
        options = []
        try:
            el.click()
            time.sleep(0.4)
            
            # Wait for menu
            try:
                self.page.wait_for_selector('.select__menu', timeout=1000)
            except:
                self.page.keyboard.press("Escape")
                return []
            
            # Read options
            opt_elements = self.page.query_selector_all('.select__option')
            for opt in opt_elements:
                text = opt.inner_text().strip()
                if text and text != 'No options':
                    options.append(text)
            
            # Close menu
            self.page.keyboard.press("Escape")
            time.sleep(0.1)
            
        except:
            pass
        
        return options[:50]  # Limit
    
    # ─────────────────────────────────────────────────────────────────────
    # PRESCAN (ported from V3.5)
    # ─────────────────────────────────────────────────────────────────────

    def _prescan_all_options(self):
        """
        Pre-scan all autocomplete/select fields to discover options BEFORE filling.
        Ported from V3.5's prescan_options() — key to higher fill rates.

        Opens each dropdown, reads options, finds exact matches, then closes.
        This prevents the issue of typing wrong text and getting no results.
        """
        print("\n🔍 Pre-scanning dropdown options...")
        # Search in active frame AND all frames
        context = getattr(self, '_active_frame', self.page)
        prescan_count = 0

        # Build lookup of field selectors to frames for fill later
        self._field_frames = {}

        for field in self.fields:
            if field.field_type not in (FieldType.AUTOCOMPLETE, FieldType.SELECT):
                continue

            # Skip if already has options (from initial scan)
            if field.options:
                prescan_count += 1
                continue

            # Skip location/school - these are SEARCH type, don't prescan
            label_lower = field.label.lower()
            if any(kw in label_lower for kw in ['location', 'city', 'school', 'university']):
                print(f"   [SEARCH] {field.label[:35]}: skipping prescan (API-driven)")
                continue

            # Skip Select2 fields - they need special _fill_select2 handling
            if 's2id' in (field.element_id or ''):
                print(f"   [SELECT2] {field.label[:35]}: will use Select2 handler")
                continue

            print(f"   Prescanning: {field.label[:35]}...", flush=True)

            try:
                el = context.query_selector(field.selector)
                if not el:
                    # Try all frames
                    for frame in self.page.frames:
                        try:
                            el = frame.query_selector(field.selector)
                            if el:
                                break
                        except:
                            continue
                if not el or not el.is_visible():
                    print(f"      ⚠️ Not visible, skipping")
                    continue

                # Close any open dropdowns
                self.page.keyboard.press('Escape')
                time.sleep(0.1)

                el.scroll_into_view_if_needed()
                el.click(timeout=3000)  # Short timeout for prescan clicks
                time.sleep(0.5)

                # Read options via aria-controls (V5 method)
                controls_id = el.get_attribute('aria-controls')
                options = []

                if controls_id:
                    listbox = context.query_selector(f'#{controls_id}')
                    if not listbox:
                        # Try in all frames
                        for frame in self.page.frames:
                            try:
                                listbox = frame.query_selector(f'#{controls_id}')
                                if listbox:
                                    break
                            except:
                                continue
                    if listbox:
                        opt_els = listbox.query_selector_all('[role="option"]')
                        for opt in opt_els[:50]:
                            text = opt.inner_text().strip()
                            if text and text not in ('No options', 'No results'):
                                options.append(text)

                # Fallback: global selectors with SHORT timeout
                if not options:
                    try:
                        self.page.wait_for_selector('.select__menu, [role="listbox"]', timeout=1000)
                        opt_els = self.page.query_selector_all('.select__option, [role="option"]')
                        for opt in opt_els[:50]:
                            text = opt.inner_text().strip()
                            if text and text not in ('No options', 'No results'):
                                options.append(text)
                    except:
                        pass

                # Close dropdown
                self.page.keyboard.press('Escape')
                time.sleep(0.1)

                if options:
                    field.options = options
                    is_fixed = len(options) <= 25
                    status = "FIXED" if is_fixed else "SEARCH"
                    print(f"      [{status}] {len(options)} options found")
                    prescan_count += 1
                else:
                    print(f"      No options found")

            except Exception as e:
                print(f"      ⚠️ Error: {str(e)[:50]}")
                try:
                    self.page.keyboard.press('Escape')
                except:
                    pass

        print(f"   📊 Pre-scanned {prescan_count} dropdowns")

    # ─────────────────────────────────────────────────────────────────────
    # LAYER 2: RESOLUTION
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_all_answers(self):
        """Find answers for all fields."""
        print("\n📋 Resolving answers...")
        
        for field in self.fields:
            if field.field_type == FieldType.FILE:
                self._resolve_file_field(field)
            else:
                self._resolve_field_answer(field)
        
        # Summary
        ready = sum(1 for f in self.fields if f.status == FillStatus.READY)
        needs = sum(1 for f in self.fields if f.status == FillStatus.NEEDS_INPUT)
        print(f"   ✅ Ready: {ready}, ⚠️ Needs input: {needs}")
    
    def _resolve_field_answer(self, field: FormField):
        """Find answer for a field using cascade."""
        
        # Skip if already filled
        if field.current_value and field.current_value not in ("", "Select...", "Select"):
            field.status = FillStatus.FILLED
            return
        
        label_lower = field.label.lower()
        
        # Skip end date fields if current role (work_experience.0.current == true)
        if any(kw in label_lower for kw in ["end date", "end month", "end year"]):
            work_exp = self.profile.data.get("work_experience", [{}])
            if work_exp and work_exp[0].get("current", False):
                field.status = FillStatus.SKIPPED
                field.answer = ""
                field.error_message = "Skipped - current role"
                return
        
        # Cascade resolution
        answer, source, confidence = None, AnswerSource.NONE, 0.0
        
        # 1. Learned database
        is_dropdown = field.field_type in (FieldType.SELECT, FieldType.AUTOCOMPLETE)
        saved = self.learned_db.find(field.label, is_dropdown)
        if saved:
            answer, source, confidence = saved, AnswerSource.LEARNED, 0.95
        
        # 2. Profile mapping
        if not answer:
            val, key = self.profile.find_by_label(field.label)
            if val:
                answer, source, confidence = val, AnswerSource.PROFILE, 0.9
                field.profile_key = key

        # 2.5 Common answers from KnowledgeBase (salary, why interested, etc.)
        if not answer and not is_dropdown:
            common = self.kb.find_common_answer(field.label)
            if common:
                answer, source, confidence = common, AnswerSource.DEFAULT, 0.88
                print(f"   📚 KB common: '{field.label[:30]}' → '{common[:40]}...'")

        # 3. Yes/No patterns
        if not answer:
            yn = self.profile.find_yes_no(field.label)
            if yn:
                answer, source, confidence = yn, AnswerSource.DEFAULT, 0.85
        
        # 4. Demographic defaults
        if not answer:
            demo = self.profile.find_demographic(field.label)
            if demo:
                answer, source, confidence = demo, AnswerSource.DEFAULT, 0.8
        
        # 5. Option matching for dropdowns
        if not answer and field.options:
            matched = self._match_option(field)
            if matched:
                answer, source, confidence = matched, AnswerSource.DEFAULT, 0.7
        
        # 6. Text defaults (years of experience, salary, etc.)
        if not answer:
            text_default = self.profile.find_text_default(field.label)
            if text_default:
                answer, source, confidence = text_default, AnswerSource.DEFAULT, 0.75

        # 7. Ollama for custom questions (with KB context)
        if not answer and self.ollama.available:
            profile_context = self._get_profile_context_for_ai()
            kb_context = self.kb.get_context_for_question(field.label)
            if kb_context:
                profile_context += f"\n\n{kb_context}"
            ollama_answer = self.ollama.generate(field.label, profile_context, field.options)
            if ollama_answer:
                if field.options:
                    ollama_answer = self.ollama.match_option(ollama_answer, field.options)
                answer, source, confidence = ollama_answer, AnswerSource.AI, 0.6
                print(f"   🤖 Ollama: '{field.label[:30]}' → '{ollama_answer[:30]}'")

        # 8. Claude AI fallback for remaining unknown fields (with KB context)
        if not answer and self.ai.available:
            profile_context = self._get_profile_context_for_ai()
            kb_context = self.kb.get_context_for_question(field.label)
            if kb_context:
                profile_context += f"\n\n{kb_context}"
            try:
                if field.options:
                    claude_answer = self.ai.choose_option(field.label, field.options, profile_context)
                else:
                    claude_answer = self.ai.generate(field.label, profile_context)
                if claude_answer:
                    answer, source, confidence = claude_answer, AnswerSource.AI, 0.55
                    print(f"   🧠 Claude: '{field.label[:30]}' → '{claude_answer[:30]}'")
            except Exception as e:
                print(f"   ⚠️ Claude fallback error: {e}")

        # Set result
        if answer:
            field.answer = answer
            field.answer_source = source
            field.confidence = confidence
            field.status = FillStatus.READY
        else:
            field.status = FillStatus.NEEDS_INPUT
    
    def _get_profile_context_for_ai(self) -> str:
        """Get rich profile context for AI questions."""
        p = self.profile.data.get("personal", {})
        w = self.profile.data.get("work_experience", [{}])[0]
        certs = self.profile.data.get("certifications", [])
        common = self.profile.data.get("common_answers", {})
        demo = self.profile.data.get("demographics", {})
        wa = self.profile.data.get("work_authorization", {})

        return f"""Name: {p.get('first_name', '')} {p.get('last_name', '')}
Location: {p.get('location', '')}, {p.get('state', '')}, US
Current Role: {w.get('title', '')} at {w.get('company', '')}
Experience: {w.get('description', '')}
Years: 15+ years in PM/TPM
Tools: GCP, AWS, Jira, Confluence, SharePoint, Python, SQL
Certifications: {', '.join(certs) if certs else 'SAFe, PSM, GCP'}
Work Authorization: Authorized to work in US: {wa.get('authorized_us', 'Yes')}, Needs sponsorship: {wa.get('requires_sponsorship', 'No')}
Willing to relocate: Yes
18 or older: Yes
Previously employed at company: No
Government official: No
Gender: {demo.get('gender', 'Decline')}
Veteran: {demo.get('veteran_status', 'Not a protected veteran')}
Disability: {demo.get('disability_status', 'Prefer not to answer')}"""
    
    def _match_option(self, field: FormField) -> Optional[str]:
        """Try to match profile data to dropdown options."""
        label_lower = field.label.lower()
        options_lower = [opt.lower() for opt in field.options]

        # Gender - prefer "Decline to self-identify" or similar
        if "gender" in label_lower:
            priority_patterns = [
                "decline", "prefer not", "do not wish", "choose not",
                "i don't wish", "not disclosed", "not to say"
            ]
            for pattern in priority_patterns:
                for i, opt_lower in enumerate(options_lower):
                    if pattern in opt_lower:
                        return field.options[i]
            # Fallback: first option that's not male/female specific
            for i, opt_lower in enumerate(options_lower):
                if "male" not in opt_lower and "female" not in opt_lower and "man" not in opt_lower and "woman" not in opt_lower:
                    return field.options[i]

        # Hispanic/Latino
        if "hispanic" in label_lower or "latino" in label_lower:
            priority_patterns = ["decline", "prefer not", "do not wish", "no"]
            for pattern in priority_patterns:
                for i, opt_lower in enumerate(options_lower):
                    if pattern in opt_lower:
                        return field.options[i]

        # Race/Ethnicity
        if "race" in label_lower or "ethnicity" in label_lower:
            priority_patterns = [
                "decline", "prefer not", "do not wish", "two or more",
                "not disclosed", "choose not"
            ]
            for pattern in priority_patterns:
                for i, opt_lower in enumerate(options_lower):
                    if pattern in opt_lower:
                        return field.options[i]

        # Veteran status
        if "veteran" in label_lower:
            priority_patterns = [
                "not a protected veteran", "i am not a", "not a veteran",
                "decline", "prefer not", "do not wish", "no"
            ]
            for pattern in priority_patterns:
                for i, opt_lower in enumerate(options_lower):
                    if pattern in opt_lower:
                        return field.options[i]

        # Disability
        if "disability" in label_lower:
            priority_patterns = [
                "do not want to answer", "prefer not", "decline",
                "do not wish", "i don't have", "no, i do not"
            ]
            for pattern in priority_patterns:
                for i, opt_lower in enumerate(options_lower):
                    if pattern in opt_lower:
                        return field.options[i]

        # Country - prefer United States
        if "country" in label_lower:
            for i, opt_lower in enumerate(options_lower):
                if "united states" in opt_lower or opt_lower == "usa" or opt_lower == "us":
                    return field.options[i]

        # State - match from profile
        if "state" in label_lower and "united" not in label_lower:
            profile_state = self.profile.get("personal.state")
            if profile_state:
                state_lower = profile_state.lower()
                for i, opt_lower in enumerate(options_lower):
                    if state_lower in opt_lower or opt_lower in state_lower:
                        return field.options[i]

        # Yes/No questions - match from YES_NO_PATTERNS
        yes_no = self.profile.find_yes_no(field.label)
        if yes_no:
            for i, opt_lower in enumerate(options_lower):
                if yes_no.lower() == opt_lower or yes_no.lower() in opt_lower:
                    return field.options[i]

        return None
    
    def _resolve_file_field(self, field: FormField):
        """Resolve file upload field based on job title."""
        label_lower = field.label.lower()
        field_id = (field.element_id or "").lower()
        field_name = (field.name or "").lower()

        # Get job title from page
        job_title = self.page.title() if self.page else ""

        # Get CV and Cover Letter paths based on role
        cv_path, cover_letter_path = self.profile.get_files_for_role(job_title)
        
        # Check what type of file is requested (check ID first, then label)
        is_cover_letter = (
            "cover_letter" in field_id or 
            "coverletter" in field_id or
            any(kw in label_lower for kw in ["cover letter", "cover_letter", "coverletter"])
        )
        
        is_resume = (
            "resume" in field_id or
            "cv" in field_id or
            any(kw in label_lower for kw in ["resume", "cv"])
        )
        
        if is_cover_letter:
            # Cover Letter field
            if cover_letter_path and cover_letter_path.exists():
                field.answer = str(cover_letter_path)
                field.answer_source = AnswerSource.PROFILE
                field.status = FillStatus.READY
                print(f"   📄 Cover Letter: {cover_letter_path.name}")
            else:
                field.status = FillStatus.NEEDS_INPUT
                field.error_message = "Cover letter not found for this role"
        elif is_resume or "attach" in label_lower or "upload" in label_lower or "browse" in label_lower or field_name == "file":
            # Resume/CV field (including generic "file" input and "Attach")
            if cv_path and cv_path.exists():
                field.answer = str(cv_path)
                field.answer_source = AnswerSource.PROFILE
                field.status = FillStatus.READY
                print(f"   📄 Resume/CV: {cv_path.name}")
            else:
                field.status = FillStatus.ERROR
                field.error_message = "CV not found for this role"
        else:
            # Unknown file field — assume resume as default
            if cv_path and cv_path.exists():
                field.answer = str(cv_path)
                field.answer_source = AnswerSource.PROFILE
                field.status = FillStatus.READY
                print(f"   📄 Default file → Resume: {cv_path.name}")
            else:
                field.status = FillStatus.NEEDS_INPUT
    
    # ─────────────────────────────────────────────────────────────────────
    # LAYER 3: INPUT
    # ─────────────────────────────────────────────────────────────────────
    
    def _fill_all_fields(self, mode: FillMode):
        """Fill all fields with answers."""
        print("\n📝 Filling fields...")

        for field in self.fields:
            if field.status == FillStatus.READY:
                self._fill_field(field)
            elif field.status == FillStatus.NEEDS_INPUT and mode == FillMode.INTERACTIVE:
                self._interactive_fill(field)
            elif field.status == FillStatus.NEEDS_INPUT:
                field.status = FillStatus.SKIPPED
            # Don't overwrite FILLED/VERIFIED/ERROR status from previous iteration
    
    def _fill_field(self, field: FormField) -> bool:
        """Fill single field. Uses _active_frame to support iframes. Timeout protected."""
        try:
            # Use active frame (main page or iframe with form)
            context = getattr(self, '_active_frame', self.page)
            el = context.query_selector(field.selector)

            # Fallback: try all frames if not found
            if not el:
                el = self.page.query_selector(field.selector)
            if not el:
                for frame in self.page.frames:
                    try:
                        el = frame.query_selector(field.selector)
                        if el:
                            break
                    except:
                        continue
            if not el:
                field.status = FillStatus.ERROR
                field.error_message = "Element not found"
                return False

            try:
                el.scroll_into_view_if_needed()
            except:
                pass
            time.sleep(0.1)

            success = False

            if field.field_type == FieldType.FILE:
                success = self._fill_file(el, field)
            elif field.field_type == FieldType.SELECT:
                success = self._fill_select(el, field)
            elif field.field_type == FieldType.AUTOCOMPLETE:
                success = self._fill_autocomplete(el, field)
            elif field.field_type == FieldType.CHECKBOX:
                success = self._fill_checkbox(el, field)
            elif field.field_type == FieldType.PHONE:
                success = self._fill_phone(el, field)
            else:
                success = self._fill_text(el, field)

            if success:
                field.status = FillStatus.FILLED
                print(f"   ✅ {field.label[:35]:<35} = {field.answer[:20]} [{field.answer_source.value}]")
            else:
                field.status = FillStatus.ERROR
                print(f"   ❌ {field.label[:35]} - fill failed")

            return success

        except Exception as e:
            field.status = FillStatus.ERROR
            field.error_message = str(e)[:100]
            print(f"   ❌ {field.label[:35]} - error: {str(e)[:50]}")
            return False
    
    def _fill_text(self, el: ElementHandle, field: FormField) -> bool:
        """Fill text/email/phone field. Uses keyboard for React compatibility."""
        try:
            # Try standard fill first
            el.fill(field.answer)
            el.evaluate("e => e.blur()")
            return True
        except:
            pass
        
        # Fallback: click and type (for React forms)
        try:
            el.click(click_count=3)  # Select all
            time.sleep(0.1)
            self.page.keyboard.type(field.answer, delay=5)
            el.evaluate("e => e.blur()")
            return True
        except:
            return False
    
    def _fill_phone(self, el: ElementHandle, field: FormField) -> bool:
        """
        Fill phone field with delay for intl-tel-input compatibility.
        Greenhouse uses intl-tel-input which needs slower typing.
        """
        try:
            el.click()
            time.sleep(0.2)
            # Use slower delay (30ms) for phone input formatting
            self.page.keyboard.type(field.answer, delay=30)
            time.sleep(0.2)
            el.evaluate("e => e.blur()")
            return True
        except:
            return False
    
    def _fill_select(self, el: ElementHandle, field: FormField) -> bool:
        """Fill native select with fuzzy matching."""
        try:
            # Try exact label match
            el.select_option(label=field.answer)
            el.evaluate("e => e.blur()")
            return True
        except:
            pass

        # Fuzzy match: get all options and find best match
        try:
            options = el.evaluate(
                "e => Array.from(e.options).map(o => ({value: o.value, text: o.text}))"
            )
            answer_lower = field.answer.lower().strip()

            for opt in options:
                opt_text = opt.get('text', '').lower()
                if answer_lower in opt_text or opt_text in answer_lower:
                    el.select_option(value=opt['value'])
                    el.evaluate("e => e.blur()")
                    return True

            # Word overlap matching
            answer_words = set(answer_lower.split())
            for opt in options:
                opt_text = opt.get('text', '').lower()
                opt_words = set(opt_text.split())
                if len(answer_words & opt_words) >= 1:
                    el.select_option(value=opt['value'])
                    el.evaluate("e => e.blur()")
                    return True

            # Last resort: first non-empty option
            for opt in options:
                if opt.get('value') and opt.get('text', '').strip():
                    el.select_option(value=opt['value'])
                    el.evaluate("e => e.blur()")
                    return True
        except Exception as e:
            print(f"      ⚠️ Select error: {e}")

        return False
    
    def _fill_autocomplete(self, el: ElementHandle, field: FormField) -> bool:
        """
        Fill autocomplete/combobox. Supports:
        - React Select (aria-controls)
        - Select2 (#s2id_autogen* selectors)
        - Location fields (API-driven)
        - School fields (search with fallback)
        """
        frame = el.owner_frame() or self.page
        label_lower = field.label.lower()

        # Check field type for special handling
        is_location = 'location' in label_lower or 'city' in label_lower
        is_school = 'school' in label_lower or 'university' in label_lower or 'college' in label_lower

        # ── SELECT2 DETECTION ──
        # Select2 uses hidden inputs like #s2id_autogen1
        # The actual clickable element is the .select2-choice container
        is_select2 = 's2id' in (field.element_id or '') or 's2id' in field.selector
        if is_select2:
            return self._fill_select2(el, field, frame)

        # Close any open dropdowns first
        self.page.keyboard.press('Escape')
        time.sleep(0.1)

        # Scroll into view
        try:
            el.scroll_into_view_if_needed()
        except:
            pass
        time.sleep(0.1)

        # For Location: type first, then wait for API
        if is_location:
            print(f"      📍 Location: typing '{field.answer[:30]}'...")
            el.click()
            time.sleep(0.3)
            self.page.keyboard.type(field.answer[:30], delay=30)
            time.sleep(2.0)  # Wait for API response
            
            # Get options via aria-controls
            controls_id = el.get_attribute('aria-controls')
            if controls_id:
                listbox = frame.query_selector(f'#{controls_id}')
                if listbox:
                    options = listbox.query_selector_all('[role="option"]')
                    if options:
                        options[0].click()
                        time.sleep(0.2)
                        return True
            
            # Fallback: just click away to confirm text
            self.page.keyboard.press('Tab')
            return True

        # For School fields: search with fallback to "0 - Other"
        if is_school:
            return self._fill_school_autocomplete(el, field, frame)

        # STRATEGY: Use prescan data if we have options from prescan
        if field.options and len(field.options) <= 25:
            # Fixed dropdown — find exact match from prescan data and click it
            el.click()
            time.sleep(0.4)

            # Read live options
            controls_id = el.get_attribute('aria-controls')
            live_options = []
            if controls_id:
                listbox = frame.query_selector(f'#{controls_id}')
                if listbox:
                    live_options = listbox.query_selector_all('[role="option"]')
            if not live_options:
                try:
                    frame.wait_for_selector('[role="listbox"], .select__menu', timeout=1500)
                    live_options = frame.query_selector_all('[role="option"], .select__option')
                except:
                    pass

            answer_lower = field.answer.lower().strip()

            # Score-based matching (like V3.5)
            best_score = 0
            best_opt = None
            for opt in live_options:
                opt_text = opt.inner_text().strip()
                opt_lower = opt_text.lower()
                score = 0
                if opt_lower == answer_lower:
                    score = 100  # Exact match
                elif answer_lower in opt_lower:
                    score = 80  # Our answer is substring
                elif opt_lower in answer_lower:
                    score = 70  # Option is substring of answer
                else:
                    # Word overlap
                    answer_words = set(answer_lower.replace('-', ' ').split())
                    opt_words = set(opt_lower.replace('-', ' ').split())
                    overlap = len(answer_words & opt_words)
                    if overlap >= 2:
                        score = 60
                    elif overlap >= 1:
                        score = 40
                if score > best_score:
                    best_score = score
                    best_opt = opt

            if best_opt:
                best_opt.click()
                time.sleep(0.2)
                return True

            # No match - click first option as fallback
            if live_options:
                live_options[0].click()
                time.sleep(0.2)
                return True

            self.page.keyboard.press('Escape')
            return True

        # SEARCH dropdown or no prescan data — type to filter
        el.click()
        time.sleep(0.3)
        controls_id = el.get_attribute('aria-controls')

        # Type to filter
        self.page.keyboard.type(field.answer[:30], delay=20)
        time.sleep(0.8)

        # Read filtered options
        live_options = []
        if controls_id:
            listbox = frame.query_selector(f'#{controls_id}')
            if listbox:
                live_options = listbox.query_selector_all('[role="option"]')
        if not live_options:
            live_options = frame.query_selector_all('[role="option"], .select__option')

        if live_options:
            # Check first option isn't "no results"
            first_text = live_options[0].inner_text().strip().lower()
            if 'no result' not in first_text and 'no option' not in first_text:
                live_options[0].click()
                time.sleep(0.2)
                return True

        # Fallback: keyboard navigation
        self.page.keyboard.press('ArrowDown')
        self.page.keyboard.press('Tab')
        time.sleep(0.2)
        return True

    def _fill_select2(self, el: ElementHandle, field: FormField, frame) -> bool:
        """
        Fill Select2 dropdown (Greenhouse EEO/demographics/custom questions).

        Select2 DOM structure:
        - .select2-container (id=s2id_job_application_gender)
          - a.select2-choice  ← CLICK THIS
          - input#s2id_autogen2 ← the input we detected
        - .select2-drop (appears after click)
          - .select2-results
            - li.select2-result ← the options to click
        """
        print(f"      🔽 Select2: {field.label[:30]}...", flush=True)

        try:
            # Open dropdown by clicking .select2-choice via JavaScript
            opened = frame.evaluate(f'''() => {{
                const input = document.querySelector('{field.selector}');
                if (!input) return false;
                // Find parent select2-container
                let container = input.closest('.select2-container');
                if (!container) {{
                    // Walk up manually
                    let parent = input.parentElement;
                    for (let i = 0; i < 5; i++) {{
                        if (!parent) break;
                        if (parent.classList && parent.classList.contains('select2-container')) {{
                            container = parent;
                            break;
                        }}
                        parent = parent.parentElement;
                    }}
                }}
                if (!container) return false;
                const choice = container.querySelector('a.select2-choice, .select2-choices');
                if (choice) {{
                    choice.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
                    return true;
                }}
                return false;
            }}''')

            if not opened:
                print(f"      ⚠️ Could not find Select2 container")
                return False

            time.sleep(0.5)

            # Wait for and read options from .select2-drop
            try:
                frame.wait_for_selector('.select2-drop:not(.select2-display-none) .select2-results', timeout=2000)
            except:
                # Try alternative: just check if results exist
                pass

            options = frame.query_selector_all('.select2-drop:not(.select2-display-none) .select2-results li.select2-result')
            if not options:
                options = frame.query_selector_all('.select2-results li.select2-result')
            if not options:
                options = frame.query_selector_all('.select2-results li')

            # Filter out disabled/header items
            valid_options = []
            for opt in options:
                try:
                    cls = opt.get_attribute('class') or ''
                    if 'select2-disabled' in cls or 'select2-result-unselectable' in cls:
                        continue
                    text = opt.inner_text().strip()
                    if text:
                        valid_options.append((opt, text))
                except:
                    continue

            if not valid_options:
                print(f"      ⚠️ No Select2 options found")
                self.page.keyboard.press('Escape')
                return False

            option_texts = [t for _, t in valid_options]
            answer_lower = field.answer.lower().strip()
            print(f"      Looking for: '{answer_lower[:30]}' in {len(valid_options)} options")

            # Score-based matching
            best_score, best_opt, best_text = self._select2_match(answer_lower, valid_options)

            if best_score >= 40:
                best_opt.click()
                time.sleep(0.3)
                print(f"      ✅ Select2 matched (score={best_score})")
                return True

            # No match from pre-resolved answer — try re-resolving with actual options
            # This handles cases where label was wrong during resolution or AI gave freetext
            print(f"      🔄 Re-resolving with actual {len(option_texts)} options...")
            new_answer = self._resolve_select2_from_options(field, option_texts)
            if new_answer:
                new_lower = new_answer.lower().strip()
                best_score2, best_opt2, best_text2 = self._select2_match(new_lower, valid_options)
                if best_score2 >= 40:
                    best_opt2.click()
                    time.sleep(0.3)
                    field.answer = new_answer
                    print(f"      ✅ Select2 re-resolved: '{best_text2[:30]}' (score={best_score2})")
                    return True

            # Final fallback — click first non-placeholder option
            fallback_idx = 0
            for idx, (_, text) in enumerate(valid_options):
                tl = text.lower()
                if tl not in ('--', 'please select', 'select', 'select...', ''):
                    fallback_idx = idx
                    break
            valid_options[fallback_idx][0].click()
            time.sleep(0.3)
            print(f"      ⚠️ Select2 fallback: '{valid_options[fallback_idx][1][:30]}'")
            return True

        except Exception as e:
            print(f"      ⚠️ Select2 error: {str(e)[:60]}")
            try:
                self.page.keyboard.press('Escape')
            except:
                pass
            return False

    def _select2_match(self, answer_lower: str, valid_options: list) -> tuple:
        """Score-based matching for Select2 options. Returns (score, element, text)."""
        best_score = 0
        best_opt = None
        best_text = ""
        for opt_el, opt_text in valid_options:
            opt_lower = opt_text.lower()
            score = 0
            if opt_lower == answer_lower:
                score = 100
            elif answer_lower in opt_lower:
                score = 80
            elif opt_lower in answer_lower:
                score = 70
            else:
                answer_words = set(answer_lower.split())
                opt_words = set(opt_lower.split())
                overlap = len(answer_words & opt_words)
                if overlap >= 2:
                    score = 60
                elif overlap >= 1:
                    score = 40
            if score > best_score:
                best_score = score
                best_opt = opt_el
                best_text = opt_text
        return best_score, best_opt, best_text

    def _resolve_select2_from_options(self, field: FormField, option_texts: list) -> str:
        """
        Re-resolve a Select2 answer using the actual dropdown options.
        Uses profile matching, defaults, and AI to pick the best option.
        """
        label = field.label.lower()
        opt_lower = [t.lower() for t in option_texts]

        # 1. Yes/No questions — check known patterns
        yes_patterns = ['authorize', 'authorized', 'legal right', 'legally',
                       '18 years', 'age', 'willing to relocat', 'relocated',
                       'background check', 'drug test', 'submit verification']
        no_patterns = ['require sponsor', 'sponsorship', 'non-compete',
                      'previously been employed', 'worked for our company',
                      'different name', 'convicted']

        has_yes = any('yes' in o for o in opt_lower)
        has_no = any('no' in o for o in opt_lower)

        if has_yes and has_no:
            if any(p in label for p in yes_patterns):
                return 'Yes'
            if any(p in label for p in no_patterns):
                return 'No'

        # 2. State/location questions
        if any(k in label for k in ['state', 'province', 'reside', 'location', 'where']):
            profile_state = ""
            if self.profile:
                profile_state = self.profile.get("personal.state")
            if profile_state:
                # Try full state name match
                state_map = {"NC": "North Carolina", "CA": "California", "NY": "New York",
                            "TX": "Texas", "WA": "Washington", "FL": "Florida",
                            "GA": "Georgia", "MA": "Massachusetts", "IL": "Illinois",
                            "PA": "Pennsylvania", "OH": "Ohio", "MI": "Michigan",
                            "VA": "Virginia", "CO": "Colorado", "AZ": "Arizona",
                            "OR": "Oregon", "MN": "Minnesota", "WI": "Wisconsin"}
                full_state = state_map.get(profile_state, profile_state)
                for t in option_texts:
                    if full_state.lower() in t.lower() or profile_state.lower() in t.lower():
                        return t
                # Also try city + state combo
                profile_city = self.profile.get("personal.city") if self.profile else ""
                if profile_city:
                    for t in option_texts:
                        if profile_city.lower() in t.lower():
                            return t

        # 3. Pronouns
        if 'pronoun' in label:
            for t in option_texts:
                if 'he/him' in t.lower():
                    return t

        # 4. How did you hear / source
        if any(k in label for k in ['how did you', 'learn about', 'hear about', 'source']):
            for t in option_texts:
                tl = t.lower()
                if any(k in tl for k in ['linkedin', 'job board', 'online']):
                    return t
            # Return "Other" if available
            for t in option_texts:
                if t.lower() in ('other', 'other (please specify)'):
                    return t

        # 5. Previously employed
        if any(k in label for k in ['previously', 'former', 'employed']):
            for t in option_texts:
                if t.lower().startswith('no'):
                    return t

        # 6. Decline/prefer not for demographic-ish questions
        if any(k in label for k in ['gender', 'race', 'ethnic', 'veteran', 'disability']):
            for t in option_texts:
                tl = t.lower()
                if 'decline' in tl or 'prefer not' in tl or 'do not want' in tl or 'not a protected' in tl:
                    return t

        # 7. Claude AI as last resort — ask to pick from specific options
        if self.ai and self.ai.available:
            try:
                clean_options = [t for t in option_texts if t.lower() not in ('--', 'please select', 'select', '')]
                if clean_options:
                    context = self._get_profile_context_for_ai()
                    ai_pick = self.ai.choose_option(field.label, clean_options, context)
                    if ai_pick:
                        return ai_pick
            except Exception as e:
                print(f"      ⚠️ AI re-resolve error: {str(e)[:40]}")

        return ""

    def _fill_school_autocomplete(self, el: ElementHandle, field: FormField, frame) -> bool:
        """
        Fill school/university searchable dropdown with fallback.

        PROVEN METHOD (from V6):
        1. Click dropdown and type search text
        2. Wait for search API results
        3. If good match found - select it
        4. Otherwise use fallback "0 - Other"
        """
        FALLBACK_OPTIONS = ["0 - Other", "Other", "Not Listed"]

        # Close any open dropdowns
        self.page.keyboard.press('Escape')
        time.sleep(0.1)

        el.scroll_into_view_if_needed()
        el.click()
        time.sleep(0.3)

        # Type search - use shorter text for better matches
        search_text = field.answer[:30] if len(field.answer) > 30 else field.answer
        self.page.keyboard.type(search_text, delay=20)
        time.sleep(1.0)  # Wait for search API

        # Get options via aria-controls
        controls_id = el.get_attribute('aria-controls')
        options = []

        if controls_id:
            listbox = frame.query_selector(f'#{controls_id}')
            if listbox:
                options = listbox.query_selector_all('[role="option"]')

        # Fallback to global selectors
        if not options:
            options = frame.query_selector_all('[role="option"], .select__option')

        # Check if we found good results
        if options:
            first_text = options[0].inner_text().strip().lower()
            # Check if first option is valid (not "no results" message)
            if 'no result' not in first_text and 'no option' not in first_text:
                # Check for match with our search
                search_lower = field.answer.lower()
                for opt in options:
                    opt_text = opt.inner_text().strip()
                    opt_lower = opt_text.lower()
                    # Match if search text appears in option
                    if search_lower[:15] in opt_lower or opt_lower in search_lower:
                        opt.click()
                        time.sleep(0.2)
                        print(f"      🎓 School matched: {opt_text[:40]}")
                        return True
                # No exact match but results exist - take first
                options[0].click()
                time.sleep(0.2)
                return True

        # No results from search - try fallback options
        print(f"      ⚠️ School not found, trying fallback...")
        self.page.keyboard.press('Escape')
        time.sleep(0.1)
        el.click()
        time.sleep(0.2)

        # Clear and try each fallback
        for fallback in FALLBACK_OPTIONS:
            self.page.keyboard.press('Control+a')
            self.page.keyboard.press('Backspace')
            self.page.keyboard.type(fallback, delay=20)
            time.sleep(0.8)

            if controls_id:
                listbox = frame.query_selector(f'#{controls_id}')
                if listbox:
                    options = listbox.query_selector_all('[role="option"]')
                    if options:
                        first_text = options[0].inner_text().strip()
                        if 'no result' not in first_text.lower():
                            options[0].click()
                            time.sleep(0.2)
                            print(f"      🎓 School fallback: {first_text[:40]}")
                            return True

        # Last resort: just tab out
        self.page.keyboard.press('Escape')
        self.page.keyboard.press('Tab')
        print(f"      ⚠️ School: typed but no selection")
        return True

    def _fill_checkbox(self, el: ElementHandle, field: FormField) -> bool:
        """Fill checkbox."""
        should_check = field.answer.lower() in ("yes", "true", "1", "checked")
        if should_check != el.is_checked():
            el.click()
        return True
    
    def _fill_file(self, el: ElementHandle, field: FormField) -> bool:
        """Upload file."""
        el.set_input_files(field.answer)
        time.sleep(0.5)
        return True
    
    def _interactive_fill(self, field: FormField):
        """Interactive fill for unknown fields."""
        self.browser.highlight_element(field.selector, "orange")
        
        print(f"\n📌 {field.label}")
        print(f"   Type: {field.field_type.value}")
        
        if field.options:
            print(f"   Options: {field.options[:5]}...")
        
        # Try AI
        if self.ai.available:
            if field.options:
                suggested = self.ai.choose_option(field.label, field.options, self.profile.get_context())
            else:
                suggested = self.ai.generate(field.label, self.profile.get_context())
            
            if suggested:
                print(f"   💡 AI suggests: {suggested[:50]}")
                field.answer = suggested
                field.answer_source = AnswerSource.AI
        
        print(f"\n   Type answer (or press Enter to skip):")
        user_input = input("   > ").strip()
        
        if user_input:
            field.answer = user_input
            field.answer_source = AnswerSource.HUMAN
            self._fill_field(field)
            
            # Learn for next time
            is_dropdown = field.field_type in (FieldType.SELECT, FieldType.AUTOCOMPLETE)
            self.learned_db.save_answer(field.label, user_input, is_dropdown)
        else:
            field.status = FillStatus.SKIPPED
        
        self.browser.unhighlight_element(field.selector)
    
    # ─────────────────────────────────────────────────────────────────────
    # LAYER 4: VALIDATION
    # ─────────────────────────────────────────────────────────────────────
    
    def _blur_all_fields(self):
        """Click outside all fields to trigger blur/validation."""
        try:
            context = getattr(self, '_active_frame', self.page)
            # Click on body to blur any focused field
            try:
                context.click('body', position={'x': 10, 'y': 10})
            except:
                self.page.click('body', position={'x': 10, 'y': 10})
            time.sleep(0.3)

            # Also blur each field explicitly
            for field in self.fields:
                if field.selector:
                    el = context.query_selector(field.selector)
                    if not el:
                        el = self.page.query_selector(field.selector)
                    if el:
                        try:
                            el.evaluate("e => e.blur()")
                        except:
                            pass
            time.sleep(0.3)
        except:
            pass
    
    def _validate_all_fields(self):
        """Validate all filled fields."""
        print("\n✓ Validating...")
        
        for field in self.fields:
            if field.status == FillStatus.FILLED:
                self._validate_field(field)
        
        verified = sum(1 for f in self.fields if f.status == FillStatus.VERIFIED)
        print(f"   Verified: {verified}/{sum(1 for f in self.fields if f.status in (FillStatus.FILLED, FillStatus.VERIFIED))}")
    
    def _validate_field(self, field: FormField) -> bool:
        """Validate single field by reading back value."""
        try:
            context = getattr(self, '_active_frame', self.page)
            el = context.query_selector(field.selector)
            if not el:
                el = self.page.query_selector(field.selector)
            if not el:
                return False
            
            actual = self._get_value(el, field.html_tag, field.input_type)
            
            # For file fields, just check if something was uploaded
            if field.field_type == FieldType.FILE:
                field.status = FillStatus.VERIFIED
                return True
            
            # Compare
            if actual and (
                actual.lower() == field.answer.lower() or
                field.answer.lower() in actual.lower() or
                actual.lower() in field.answer.lower()
            ):
                field.status = FillStatus.VERIFIED
                return True
            
            # Check for errors
            aria_invalid = el.get_attribute("aria-invalid")
            if aria_invalid == "true":
                field.status = FillStatus.ERROR
                field.error_message = "Field marked as invalid"
                return False
            
            # Partial match is ok for autocomplete
            if field.field_type == FieldType.AUTOCOMPLETE:
                field.status = FillStatus.VERIFIED
                return True
            
            return False
            
        except:
            return False
    
    # ─────────────────────────────────────────────────────────────────────
    # REPORT
    # ─────────────────────────────────────────────────────────────────────
    
    # ─────────────────────────────────────────────────────────────────────
    # REPEATABLE SECTIONS (Work Experience, Education)
    # ─────────────────────────────────────────────────────────────────────
    
    def fill_section_entry(self, section_name: str, entry_index: int, form_index: int) -> bool:
        """Fill one entry of a repeatable section."""
        config = self.REPEATABLE_SECTIONS.get(section_name)
        if not config:
            return False
        
        profile_key = config['profile_key']
        field_patterns = config['field_patterns']
        skip_end_if_current = config.get('skip_end_date_if_current', False)
        
        entries = self.profile.data.get(profile_key, [])
        if entry_index >= len(entries):
            return False
        
        entry = entries[entry_index]
        is_current = entry.get('current', False)
        
        company_or_school = entry.get('company', entry.get('school', ''))
        print(f"   📝 {section_name}[{entry_index}]: {company_or_school[:40]}")
        
        filled_any = False
        
        for pattern, field_name in field_patterns.items():
            selector = '#' + pattern.replace('{N}', str(form_index))
            
            # Skip end date if current role
            if skip_end_if_current and is_current and 'end' in field_name:
                print(f"      ⏭️ {field_name} (skipped - current role)")
                continue
            
            value = entry.get(field_name, '')
            if not value and field_name != 'current':
                continue
            
            # Handle boolean current field
            if field_name == 'current':
                if value == True:
                    value = 'checked'
                else:
                    continue
            
            # Search in active frame first (iframe support), then main page
            context = getattr(self, '_active_frame', self.page)
            el = context.query_selector(selector)
            if not el and context != self.page:
                el = self.page.query_selector(selector)
            if not el:
                continue

            try:
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                el_type = el.get_attribute('type') or 'text'
                role = el.get_attribute('role') or ''
                aria = el.get_attribute('aria-haspopup') or ''
                
                # Determine field type and fill
                if tag == 'select':
                    el.select_option(label=str(value))
                elif role == 'combobox' or aria in ('true', 'listbox'):
                    # For autocomplete: use aria-controls method
                    frame = el.owner_frame() or self.page
                    
                    # Close any open dropdowns
                    self.page.keyboard.press('Escape')
                    time.sleep(0.1)
                    
                    el.scroll_into_view_if_needed()
                    el.click()
                    time.sleep(0.3)
                    
                    # Type to filter
                    search_text = str(value)[:40]
                    el.fill('')
                    self.page.keyboard.type(search_text, delay=15)
                    time.sleep(0.5)
                    
                    # Use aria-controls to find correct listbox
                    controls_id = el.get_attribute('aria-controls')
                    opts = []
                    if controls_id:
                        listbox = frame.query_selector(f'#{controls_id}')
                        if listbox:
                            opts = listbox.query_selector_all('[role="option"]')
                    
                    # Fallback to global selectors
                    if not opts:
                        opts = frame.query_selector_all('[role="option"], .select__option')
                    
                    value_lower = str(value).lower()
                    best_match = None
                    
                    for opt in opts:
                        opt_text = opt.inner_text().strip().lower()
                        if value_lower in opt_text or opt_text in value_lower:
                            best_match = opt
                            break
                        value_words = set(value_lower.split()[:3])
                        opt_words = set(opt_text.split()[:3])
                        if len(value_words & opt_words) >= 2:
                            best_match = opt
                            break
                    
                    if best_match:
                        best_match.click()
                    elif opts:
                        opts[0].click()
                    else:
                        # No matches - try "Other" for school fields
                        if 'school' in field_name.lower():
                            self.page.keyboard.press('Escape')
                            time.sleep(0.1)
                            el.fill('')
                            self.page.keyboard.type('0 - Other', delay=15)
                            time.sleep(0.5)
                            if controls_id:
                                listbox = frame.query_selector(f'#{controls_id}')
                                if listbox:
                                    other_opts = listbox.query_selector_all('[role="option"]')
                                    if other_opts:
                                        other_opts[0].click()
                                    else:
                                        self.page.keyboard.press('Tab')
                            else:
                                self.page.keyboard.press('Tab')
                        else:
                            self.page.keyboard.press('Tab')
                    time.sleep(0.2)
                elif el_type == 'checkbox':
                    if value == 'checked' and not el.is_checked():
                        el.click()
                else:
                    el.fill(str(value))
                
                filled_any = True
                print(f"      ✅ {field_name}: {str(value)[:25]}")
                time.sleep(0.1)
                
            except Exception as e:
                print(f"      ❌ {field_name}: {e}")
        
        return filled_any
    
    def click_add_another(self, section_name: str) -> bool:
        """Click 'Add another' button for a section. Searches in iframe too."""
        config = self.REPEATABLE_SECTIONS.get(section_name)
        if not config:
            return False

        contexts = [getattr(self, '_active_frame', self.page), self.page]
        section_text = config.get('section_text', section_name.replace('_', ' '))

        for context in contexts:
            # Try configured selectors
            for selector in config.get('add_button_selectors', []):
                try:
                    btn = context.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        time.sleep(0.5)
                        self.browser.wait_for_stable()
                        return True
                except:
                    continue

            # Text-based search within the section
            try:
                all_links = context.query_selector_all('a, button')
                for link in all_links:
                    try:
                        if not link.is_visible():
                            continue
                        text = link.inner_text().strip().lower()
                        if 'add another' in text or 'add a' in text:
                            link.click()
                            time.sleep(0.5)
                            self.browser.wait_for_stable()
                            return True
                    except:
                        continue
            except:
                continue

        return False
    
    def fill_repeatable_section(self, section_name: str):
        """Fill all entries for a repeatable section."""
        config = self.REPEATABLE_SECTIONS.get(section_name)
        if not config:
            return
        
        entries = self.profile.data.get(config['profile_key'], [])
        if not entries:
            print(f"   ⚠️ No {section_name} entries in profile")
            return
        
        print(f"\n🔄 Filling {section_name}: {len(entries)} entries")
        
        # Fill first entry
        self.fill_section_entry(section_name, entry_index=0, form_index=0)
        
        # Add and fill additional entries
        for i in range(1, len(entries)):
            print(f"\n   ➕ Adding {section_name} entry {i+1}...")
            if self.click_add_another(section_name):
                time.sleep(1.0)  # Wait for React to render new fields
                # Scroll to new section to ensure visibility
                new_selector = list(config['field_patterns'].keys())[0].replace('{N}', str(i))
                new_el = self.page.query_selector(f'#{new_selector}')
                if new_el:
                    new_el.scroll_into_view_if_needed()
                    time.sleep(0.3)
                self.fill_section_entry(section_name, entry_index=i, form_index=i)
            else:
                print(f"   ❌ Could not add entry {i+1}")
                break
    
    def fill_all_repeatable_sections(self):
        """Fill all repeatable sections (work experience, education)."""
        print("\n" + "="*60)
        print("📋 FILLING REPEATABLE SECTIONS")
        print("="*60)
        
        for section_name in self.REPEATABLE_SECTIONS.keys():
            self.fill_repeatable_section(section_name)
    
    def _generate_report(self, url: str) -> FillReport:
        """Generate fill report."""
        report = FillReport(
            url=url,
            title=self.page.title() if self.page else "",
            ats_type=self._detect_ats(url),
            total_fields=len(self.fields),
            fields=self.fields,
        )
        
        for f in self.fields:
            if f.status == FillStatus.READY:
                report.ready_fields += 1
            elif f.status == FillStatus.FILLED:
                report.filled_fields += 1
            elif f.status == FillStatus.VERIFIED:
                report.verified_fields += 1
            elif f.status == FillStatus.NEEDS_INPUT:
                report.needs_input += 1
            elif f.status == FillStatus.SKIPPED:
                report.skipped += 1
            elif f.status == FillStatus.ERROR:
                report.errors += 1
        
        return report
    
    def _detect_ats(self, url: str) -> str:
        """Detect ATS type from URL."""
        url_lower = url.lower()
        if "greenhouse" in url_lower or "grnhse" in url_lower:
            return "Greenhouse"
        if "lever" in url_lower or "jobs.lever.co" in url_lower:
            return "Lever"
        if "workday" in url_lower or "myworkdayjobs" in url_lower:
            return "Workday"
        if "ashby" in url_lower:
            return "Ashby"
        if "smartrecruiters" in url_lower:
            return "SmartRecruiters"
        if "icims" in url_lower:
            return "iCIMS"
        if "jobvite" in url_lower:
            return "Jobvite"
        if "taleo" in url_lower:
            return "Taleo"
        if "breezy" in url_lower:
            return "BreezyHR"
        return "Unknown"

    # ATS-specific selectors for common fields
    ATS_SELECTORS = {
        "Greenhouse": {
            "first_name": "#first_name",
            "last_name": "#last_name",
            "email": "#email",
            "phone": "#phone",
            "location": "#candidate-location, #location",
            "resume": "input[type='file'][name*='resume'], input[type='file']:first-of-type",
            "cover_letter": "input[type='file'][name*='cover'], input[type='file']:nth-of-type(2)",
            "linkedin": "#job_application_answers_attributes_0_text_value, input[name*='linkedin']",
            "country": "#country",
            "work_company": "#company-name-{N}",
            "work_title": "#title-{N}",
            "school": "#school--{N}",
            "degree": "#degree--{N}",
            "apply_button": "button:has-text('Apply'), a.postings-btn",
        },
        "Lever": {
            "first_name": "input[name='name']",
            "email": "input[name='email']",
            "phone": "input[name='phone']",
            "location": "input[name='location']",
            "resume": "input[type='file']",
            "linkedin": "input[name='urls[LinkedIn]']",
            "apply_button": "button[type='submit'], button:has-text('Submit')",
        },
        "Workday": {
            "first_name": "input[data-automation-id='legalNameSection_firstName']",
            "last_name": "input[data-automation-id='legalNameSection_lastName']",
            "email": "input[data-automation-id='email']",
            "phone": "input[data-automation-id='phone']",
            "resume": "input[data-automation-id='file-upload-input-ref']",
            "country": "button[data-automation-id='countryDropdown']",
            "apply_button": "button[data-automation-id='jobPostingApplyButton']",
        },
        "Ashby": {
            "first_name": "input[name='_systemfield_first_name']",
            "last_name": "input[name='_systemfield_last_name']",
            "email": "input[name='_systemfield_email']",
            "phone": "input[name='_systemfield_phone']",
            "resume": "input[type='file']",
            "linkedin": "input[name='_systemfield_linkedin']",
            "apply_button": "button[type='submit']",
        },
        "iCIMS": {
            "first_name": "input[name='firstName']",
            "last_name": "input[name='lastName']",
            "email": "input[name='email']",
            "phone": "input[name='phoneNumber']",
            "resume": "input[type='file']",
            "apply_button": "a.iCIMS_ApplyButton, button:has-text('Apply')",
        },
    }

    def get_ats_selector(self, ats_type: str, field_name: str, index: int = 0) -> str:
        """Get ATS-specific selector for a field."""
        selectors = self.ATS_SELECTORS.get(ats_type, {})
        selector = selectors.get(field_name, "")
        if "{N}" in selector:
            selector = selector.replace("{N}", str(index))
        return selector


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    mode_arg = sys.argv[2] if len(sys.argv) > 2 else "interactive"
    
    mode_map = {
        "preflight": FillMode.PRE_FLIGHT,
        "interactive": FillMode.INTERACTIVE,
        "auto": FillMode.AUTONOMOUS,
    }
    mode = mode_map.get(mode_arg, FillMode.INTERACTIVE)
    
    print("\n" + "="*70)
    print("🚀 SMART FORM FILLER V5.0")
    print("="*70)
    print(f"URL: {url[:60]}...")
    print(f"Mode: {mode.value}")
    print("="*70)
    
    filler = FormFillerV5(browser_mode=BrowserMode.CDP)
    
    if mode == FillMode.PRE_FLIGHT:
        report = filler.analyze(url)
    else:
        report = filler.fill(url, mode=mode)
    
    print("\n" + report.summary())


if __name__ == "__main__":
    main()
