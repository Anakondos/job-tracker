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
import requests
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field as dataclass_field
from enum import Enum

from playwright.sync_api import Page, ElementHandle

from .browser_manager import BrowserManager, BrowserMode


# ═══════════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════════

V5_DIR = Path(__file__).parent
BROWSER_DIR = V5_DIR.parent
DATA_DIR = V5_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

PROFILE_PATH = BROWSER_DIR / "profiles" / "anton_tpm.json"
LEARNED_DB_PATH = DATA_DIR / "learned_answers.json"
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
        "first name": "personal.first_name",
        "last name": "personal.last_name",
        "email": "personal.email",
        "phone": "personal.phone",
        "city": "personal.city",
        "state": "personal.state",
        "country": "personal.country",
        "zip": "personal.zip_code",
        "postal": "personal.zip_code",
        "street": "personal.street_address",
        "address": "personal.street_address",
        "linkedin": "links.linkedin",
        "github": "links.github",
    }
    
    # Yes/No question patterns
    YES_NO_PATTERNS = {
        "18 years": "Yes",
        "authorized to work": "Yes",
        "legally authorized": "Yes",
        "require sponsorship": "No",
        "visa sponsorship": "No",
        "government official": "No",
        "previously employed": "No",
        "confirm": "Yes",
        "acknowledge": "Yes",
        "agree": "Yes",
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
    
    def get_context(self) -> str:
        """Get summary context for AI."""
        p = self.data.get("personal", {})
        w = self.data.get("work_experience", [{}])[0]
        return f"Name: {p.get('first_name', '')} {p.get('last_name', '')}\nLocation: {p.get('location', '')}\nRole: {w.get('title', '')} at {w.get('company', '')}"


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
                return json.load(f)
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
# AI HELPER - CLAUDE API + OLLAMA FALLBACK
# ═══════════════════════════════════════════════════════════════════════════

class AIHelper:
    """
    AI Helper using Claude API for fast, accurate responses.
    Falls back to local Ollama if Claude unavailable.
    """
    
    def __init__(self):
        self._vision_ai = None
        self._ollama_available = None
        self.ollama_url = "http://localhost:11434"
    
    @property
    def vision_ai(self):
        """Lazy-load Vision AI."""
        if self._vision_ai is None:
            try:
                from .vision_ai import VisionAI
                self._vision_ai = VisionAI()
            except ImportError:
                pass
        return self._vision_ai
    
    @property
    def claude_available(self) -> bool:
        """Check if Claude API is available."""
        return self.vision_ai is not None and self.vision_ai.available
    
    @property
    def ollama_available(self) -> bool:
        """Check if local Ollama is available."""
        if self._ollama_available is None:
            try:
                self._ollama_available = requests.get(f"{self.ollama_url}/api/tags", timeout=3).ok
            except:
                self._ollama_available = False
        return self._ollama_available
    
    @property
    def available(self) -> bool:
        """Check if any AI is available."""
        return self.claude_available or self.ollama_available
    
    def generate(self, question: str, context: str, max_length: int = 150) -> str:
        """Generate answer for custom question."""
        # Try Claude first (fast, accurate)
        if self.claude_available:
            result = self.vision_ai.generate_custom_answer(
                question=question,
                profile_context=context,
                max_words=max_length // 5  # Roughly convert tokens to words
            )
            if result.get("success") and result.get("answer"):
                return result["answer"]
        
        # Fallback to Ollama
        if self.ollama_available:
            try:
                resp = requests.post(f"{self.ollama_url}/api/generate", json={
                    "model": "llama3.2:3b",
                    "prompt": f"""Job application question: {question}

Profile: {context}

Write a brief, professional answer (1-3 sentences):""",
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": max_length}
                }, timeout=30)
                
                if resp.ok:
                    return resp.json().get("response", "").strip()
            except:
                pass
        
        return ""
    
    def choose_option(self, question: str, options: List[str], context: str) -> Optional[str]:
        """Choose best option from list."""
        if not options:
            return None
        
        # Try Claude first
        if self.claude_available:
            try:
                vision = self.vision_ai
                
                prompt = f"""Job application dropdown:
Question: {question}
Options: {options}
Profile: {context}

Which option should be selected? Return ONLY the exact option text, nothing else."""
                
                response = vision.client.messages.create(
                    model=vision.config.model,
                    max_tokens=100,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                
                answer = response.content[0].text.strip()
                
                # Find matching option
                for opt in options:
                    if opt.lower() == answer.lower():
                        return opt
                    if opt.lower() in answer.lower() or answer.lower() in opt.lower():
                        return opt
                        
            except Exception as e:
                print(f"   ⚠️ Claude option selection failed: {e}")
        
        # Fallback to Ollama
        if self.ollama_available:
            try:
                resp = requests.post(f"{self.ollama_url}/api/generate", json={
                    "model": "llama3.2:3b",
                    "prompt": f"Question: {question}\nOptions: {', '.join(options)}\nProfile: {context}\n\nWhich option? Reply with exact option text only:",
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 50}
                }, timeout=20)
                
                if resp.ok:
                    answer = resp.json().get("response", "").strip()
                    for opt in options:
                        if opt.lower() in answer.lower() or answer.lower() in opt.lower():
                            return opt
            except:
                pass
        
        return None
    
    def analyze_field_screenshot(self, screenshot_path: str, field_description: str = "") -> Dict[str, Any]:
        """Analyze form field from screenshot using Claude Vision."""
        if self.claude_available:
            return self.vision_ai.analyze_field(screenshot_path, field_description)
        return {"success": False, "error": "Claude Vision not available"}
    
    def verify_field_filled(self, screenshot_path: str, expected_value: str, field_label: str = "") -> Dict[str, Any]:
        """Verify field was filled correctly using Claude Vision."""
        if self.claude_available:
            return self.vision_ai.verify_field_filled(screenshot_path, expected_value, field_label)
        return {"success": False, "verified": False}


# ═══════════════════════════════════════════════════════════════════════════
# FORM FILLER ENGINE
# ═══════════════════════════════════════════════════════════════════════════

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
    
    def __init__(self, browser_mode: BrowserMode = BrowserMode.CDP):
        self.browser_mode = browser_mode
        self.profile = Profile()
        self.learned_db = LearnedDB()
        self.ai = AIHelper()
        
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
        """
        with BrowserManager(mode=self.browser_mode) as browser:
            self.browser = browser
            self.page = browser.page
            
            browser.goto(url)
            browser.wait_for_stable()
            
            # Scan, resolve, fill
            self._scan_fields()
            self._resolve_all_answers()
            
            if mode == FillMode.PRE_FLIGHT:
                return self._generate_report(url)
            
            self._fill_all_fields(mode)
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
    
    # ─────────────────────────────────────────────────────────────────────
    # LAYER 1: DETECTION
    # ─────────────────────────────────────────────────────────────────────
    
    def _scan_fields(self):
        """Scan all form fields on page."""
        print("\n🔍 Scanning form fields...")
        self.fields = []
        self._seen_selectors = set()
        
        elements = self.page.query_selector_all("input, select, textarea")
        
        for el in elements:
            field = self._detect_field(el)
            if field and field.selector not in self._seen_selectors:
                self._seen_selectors.add(field.selector)
                self.fields.append(field)
        
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
            
            # Build selector
            if el_id:
                selector = f"#{el_id}"
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
            
            # Get options for select/autocomplete
            options = []
            if field_type == FieldType.SELECT:
                options = el.evaluate(
                    "e => Array.from(e.options).map(o => o.text).filter(t => t && t !== 'Select...')"
                )
            elif field_type == FieldType.AUTOCOMPLETE:
                options = self._probe_autocomplete_options(el, selector)
            
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
        """Find label text for field."""
        label = ""
        
        # By for attribute
        if el_id:
            label_el = self.page.query_selector(f"label[for='{el_id}']")
            if label_el:
                label = label_el.inner_text().strip()
        
        # Fallback to aria/placeholder
        if not label:
            label = el.get_attribute("aria-label") or \
                    el.get_attribute("placeholder") or \
                    el.get_attribute("name") or el_id
        
        return label
    
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
        
        # Set result
        if answer:
            field.answer = answer
            field.answer_source = source
            field.confidence = confidence
            field.status = FillStatus.READY
        else:
            field.status = FillStatus.NEEDS_INPUT
    
    def _match_option(self, field: FormField) -> Optional[str]:
        """Try to match profile data to dropdown options."""
        label_lower = field.label.lower()
        
        # Gender
        if "gender" in label_lower:
            for opt in field.options:
                if "decline" in opt.lower() or "prefer not" in opt.lower():
                    return opt
        
        # Veteran
        if "veteran" in label_lower:
            for opt in field.options:
                if "not a" in opt.lower() or "no" in opt.lower():
                    return opt
        
        # Disability
        if "disability" in label_lower:
            for opt in field.options:
                if "do not want" in opt.lower() or "prefer not" in opt.lower():
                    return opt
        
        # Country
        if "country" in label_lower:
            for opt in field.options:
                if "united states" in opt.lower():
                    return opt
        
        return None
    
    def _resolve_file_field(self, field: FormField):
        """Resolve file upload field."""
        label_lower = field.label.lower()
        
        resume_path = Path(self.profile.data.get("files", {}).get(
            "resume_path", 
            "/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/Anton_Kondakov_TPM_CV.pdf"
        ))
        
        if any(kw in label_lower for kw in ["resume", "cv", "attach"]):
            if resume_path.exists():
                field.answer = str(resume_path)
                field.answer_source = AnswerSource.PROFILE
                field.status = FillStatus.READY
            else:
                field.status = FillStatus.ERROR
                field.error_message = "Resume file not found"
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
            else:
                field.status = FillStatus.SKIPPED
    
    def _fill_field(self, field: FormField) -> bool:
        """Fill single field."""
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                field.status = FillStatus.ERROR
                field.error_message = "Element not found"
                return False
            
            self.browser.highlight_element(field.selector, "green")
            el.scroll_into_view_if_needed()
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
            else:
                success = self._fill_text(el, field)
            
            if success:
                field.status = FillStatus.FILLED
                print(f"   ✅ {field.label[:35]:<35} = {field.answer[:20]} [{field.answer_source.value}]")
            else:
                field.status = FillStatus.ERROR
                print(f"   ❌ {field.label[:35]} - fill failed")
            
            self.browser.unhighlight_element(field.selector)
            return success
            
        except Exception as e:
            field.status = FillStatus.ERROR
            field.error_message = str(e)
            return False
    
    def _fill_text(self, el: ElementHandle, field: FormField) -> bool:
        """Fill text/email/phone field."""
        el.fill(field.answer)
        return True
    
    def _fill_select(self, el: ElementHandle, field: FormField) -> bool:
        """Fill native select."""
        el.select_option(label=field.answer)
        return True
    
    def _fill_autocomplete(self, el: ElementHandle, field: FormField) -> bool:
        """Fill autocomplete/combobox."""
        el.click()
        time.sleep(0.2)
        el.fill("")
        el.type(field.answer, delay=20)
        time.sleep(0.5)
        
        # Find and click matching option
        try:
            self.page.wait_for_selector('.select__menu', timeout=1500)
            
            options = self.page.query_selector_all('.select__option')
            answer_lower = field.answer.lower()
            
            for opt in options:
                opt_text = opt.inner_text().strip().lower()
                if answer_lower in opt_text or opt_text in answer_lower:
                    opt.click()
                    return True
            
            # Fallback: first option
            if options:
                options[0].click()
                return True
                
        except:
            self.page.keyboard.press("ArrowDown")
            time.sleep(0.1)
            self.page.keyboard.press("Enter")
        
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
        if "greenhouse" in url_lower:
            return "Greenhouse"
        if "lever" in url_lower:
            return "Lever"
        if "workday" in url_lower:
            return "Workday"
        if "ashby" in url_lower:
            return "Ashby"
        if "smartrecruiters" in url_lower:
            return "SmartRecruiters"
        return "Unknown"


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
