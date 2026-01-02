"""
Smart Filler V3.5 - Repeatable Sections

NEW: Support for repeatable sections (Work Experience, Education):
1. Detect "Add another" buttons
2. Count entries in profile (work_experience, education)
3. Fill first entry, click "Add another", fill next
4. Repeat until all profile entries are added

Plus all V3.4 features:
- Pre-scan dropdown options
- Cascade detection
- React Select support
"""

import json
import re
import time
import requests
from pathlib import Path
from typing import Optional, List, Tuple, Set
from dataclasses import dataclass, field as dataclass_field
from enum import Enum

from playwright.sync_api import sync_playwright, Page, ElementHandle


# ═══════════════════════════════════════════════════════════════════
# PATHS & CONFIG
# ═══════════════════════════════════════════════════════════════════

BROWSER_DIR = Path(__file__).parent
DATABASE_PATH = BROWSER_DIR / "learned_database_v3.json"
FIELD_DB_PATH = BROWSER_DIR / "field_database.json"
PROFILE_PATH = BROWSER_DIR / "profiles" / "anton_tpm.json"
RESUME_PATH = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/Anton_Kondakov_TPM_CV.pdf")
COVER_LETTERS_DIR = Path("/Users/antonkondakov/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/cover_letters")


class FieldType(Enum):
    TEXT = "text"
    TEXTAREA = "textarea"
    EMAIL = "email"
    PHONE = "phone"
    DROPDOWN = "dropdown"
    AUTOCOMPLETE = "autocomplete"  # combobox with text input
    CHECKBOX = "checkbox"
    RADIO = "radio"
    DATE = "date"
    FILE = "file"
    UNKNOWN = "unknown"


@dataclass
class DetectedField:
    selector: str
    label: str
    field_type: FieldType
    detection_method: str  # html, aria, known_id, label, probe
    html_tag: str
    input_type: str
    options: List[str] = dataclass_field(default_factory=list)
    is_required: bool = False
    current_value: str = ""
    profile_key: str = ""  # e.g. "education.0.school"
    answer: str = ""
    answer_source: str = ""
    filled: bool = False
    # V3.4: Pre-scan data
    is_fixed: bool = False  # True if ≤20 options
    exact_option: str = ""  # Exact option text to select


# ═══════════════════════════════════════════════════════════════════
# FIELD DATABASE (known selectors)
# ═══════════════════════════════════════════════════════════════════

class FieldDatabase:
    """Known field patterns from field_database.json"""
    
    def __init__(self, path: Path = FIELD_DB_PATH):
        self.patterns = {}
        if path.exists():
            with open(path) as f:
                data = json.load(f)
                self.patterns = data.get("field_patterns", {})
    
    def find_by_selector(self, selector: str) -> Optional[dict]:
        """Find pattern by selector like #school--0"""
        for name, pattern in self.patterns.items():
            selectors = pattern.get("selectors", [])
            if selector in selectors:
                return {"name": name, **pattern}
        return None
    
    def find_by_label(self, label: str) -> Optional[dict]:
        """Find pattern by label text"""
        label_lower = label.lower()
        for name, pattern in self.patterns.items():
            labels = pattern.get("labels", [])
            for l in labels:
                if l.lower() in label_lower or label_lower in l.lower():
                    return {"name": name, **pattern}
        return None


# ═══════════════════════════════════════════════════════════════════
# LEARNED DATABASE
# ═══════════════════════════════════════════════════════════════════

class LearnedDatabase:
    def __init__(self, path: Path = DATABASE_PATH):
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
    
    def _key(self, label: str) -> str:
        key = label.lower().strip()
        key = re.sub(r'[*?!:\-_()\"\']+', ' ', key)
        key = re.sub(r'\s+', ' ', key).strip()
        return key[:100]
    
    def find_answer(self, label: str) -> Optional[str]:
        key = self._key(label)
        if key in self.data["answers"]:
            return self.data["answers"][key]
        for k, v in self.data["answers"].items():
            if k in key or key in k:
                return v
        return None
    
    def find_dropdown_choice(self, label: str) -> Optional[str]:
        key = self._key(label)
        if key in self.data["dropdown_choices"]:
            return self.data["dropdown_choices"][key]
        for k, v in self.data["dropdown_choices"].items():
            if k in key or key in k:
                return v
        return None
    
    def save_answer(self, label: str, answer: str):
        self.data["answers"][self._key(label)] = answer
        self.save()
        print(f"   💾 Learned: '{label[:30]}' → '{answer[:25]}'")
    
    def save_dropdown_choice(self, label: str, choice: str):
        self.data["dropdown_choices"][self._key(label)] = choice
        self.save()
        print(f"   💾 Learned dropdown: '{label[:30]}' → '{choice}'")


# ═══════════════════════════════════════════════════════════════════
# PROFILE
# ═══════════════════════════════════════════════════════════════════

class Profile:
    LABEL_TO_PROFILE = {
        "first name": "personal.first_name",
        "last name": "personal.last_name",
        "email": "personal.email",
        "phone": "personal.phone",
        "location": "personal.location",
        "city": "personal.city",
        "country": "personal.country",
        "street": "personal.street_address",
        "address line": "personal.street_address",
        "zip": "personal.zip_code",
        "postal": "personal.zip_code",
        "state": "personal.state",
        "linkedin": "links.linkedin",
        "github": "links.github",
        "company name": "work_experience.0.company",
        "employer": "work_experience.0.company",
        "job title": "work_experience.0.title",
        "title": "work_experience.0.title",
        "start date month": "work_experience.0.start_month",
        "start month": "work_experience.0.start_month",
        "start date year": "work_experience.0.start_year",
        "start year": "work_experience.0.start_year",
        "school": "education.0.school",
        "university": "education.0.school",
        "degree": "education.0.degree",
        "discipline": "education.0.discipline",
        "your major": "education.0.discipline",  # More specific than just "major"
        "field of study": "education.0.discipline",
    }
    
    YES_NO_DEFAULTS = {
        "18 years": "Yes",
        "authorized to work": "Yes",
        "legally authorized": "Yes",
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
        "acknowledge": "Yes",
        "confirm receipt": "Confirmed",  # Privacy notice confirmation
        "agree": "Yes",
        "i understand": "Yes",
        # Current role checkbox
        "current role": "Yes",
        "currently work here": "Yes",
        "i currently work": "Yes",
    }
    
    # Text field defaults
    TEXT_DEFAULTS = {
        "years of experience": "15",
        "years experience": "15",
        "how many years": "15",
        "how did you hear": "LinkedIn",
        "how did you find": "LinkedIn",
        "where did you hear": "LinkedIn",
    }
    
    DEMOGRAPHIC_DEFAULTS = {
        "gender": "Decline",
        "race": "Decline",
        "ethnicity": "Decline",
        "hispanic": "Decline",
        "latino": "Decline",
        "veteran": "not a protected veteran",
        "disability": "do not want to answer",
    }
    
    def __init__(self, path: Path = PROFILE_PATH):
        self.data = {}
        if path.exists():
            with open(path) as f:
                self.data = json.load(f)
    
    def get(self, path: str) -> str:
        parts = path.split(".")
        val = self.data
        for p in parts:
            if val is None:
                return ""
            if p.isdigit():
                val = val[int(p)] if isinstance(val, list) and int(p) < len(val) else None
            else:
                val = val.get(p) if isinstance(val, dict) else None
        return str(val) if val else ""
    
    def find_by_label(self, label: str) -> Tuple[Optional[str], Optional[str]]:
        """Returns (value, profile_key) or (None, None)
        Uses word boundary matching to avoid false positives like 'capacity' matching 'city'
        """
        import re
        ll = label.lower()
        for pattern, key in self.LABEL_TO_PROFILE.items():
            # Word boundary match: pattern must be a whole word
            if re.search(r'\b' + re.escape(pattern) + r'\b', ll):
                val = self.get(key)
                if val:
                    return val, key
        return None, None
    
    def find_yes_no(self, label: str) -> Optional[str]:
        ll = label.lower()
        for pattern, answer in self.YES_NO_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def find_demographic(self, label: str) -> Optional[str]:
        ll = label.lower()
        for pattern, answer in self.DEMOGRAPHIC_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def find_text_default(self, label: str) -> Optional[str]:
        ll = label.lower()
        for pattern, answer in self.TEXT_DEFAULTS.items():
            if pattern in ll:
                return answer
        return None
    
    def get_context(self) -> str:
        p = self.data.get("personal", {})
        w = self.data.get("work_experience", [{}])[0]
        return f"Name: {p.get('first_name', '')} {p.get('last_name', '')}\nLocation: {p.get('location', '')}\nRole: {w.get('title', '')} at {w.get('company', '')}"


# ═══════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE (experience snippets for AI)
# ═══════════════════════════════════════════════════════════════════

KNOWLEDGE_PATH = BROWSER_DIR / "knowledge_base.json"

class KnowledgeBase:
    """Knowledge base with experience snippets for AI answers."""
    
    def __init__(self, path: Path = KNOWLEDGE_PATH):
        self.data = {}
        if path.exists():
            with open(path) as f:
                self.data = json.load(f)
        self.snippets = self.data.get("experience_snippets", {})
        self.skills = self.data.get("skills", {})
    
    def find_relevant_snippets(self, question: str) -> List[str]:
        """Find relevant experience snippets for a question."""
        q_lower = question.lower()
        found = []
        
        # Search by keyword in snippets
        for keyword, snippet in self.snippets.items():
            if keyword.lower() in q_lower:
                found.append(f"{keyword}: {snippet}")
        
        # Also check skills/tools mentioned
        all_tools = self.skills.get("tools", []) + self.skills.get("methodologies", [])
        for tool in all_tools:
            if tool.lower() in q_lower and tool not in [s.split(":")[0] for s in found]:
                # Tool mentioned but no snippet - note it
                found.append(f"{tool}: (skill known, no specific snippet)")
        
        return found[:3]  # Max 3 snippets
    
    def get_context_for_question(self, question: str) -> str:
        """Get relevant context for AI prompt."""
        snippets = self.find_relevant_snippets(question)
        if snippets:
            return "Relevant experience:\n" + "\n".join(snippets)
        return ""


# ═══════════════════════════════════════════════════════════════════
# TEXT AI
# ═══════════════════════════════════════════════════════════════════

class TextAI:
    def __init__(self, model: str = "llama3.2:3b", knowledge_base: 'KnowledgeBase' = None):
        self.model = model
        self.url = "http://localhost:11434"
        self.available = self._check()
        self.kb = knowledge_base
    
    def _check(self) -> bool:
        try:
            return requests.get(f"{self.url}/api/tags", timeout=3).ok
        except:
            return False
    
    def generate(self, question: str, context: str) -> str:
        if not self.available:
            return ""
        
        # Add knowledge base context if available
        kb_context = ""
        if self.kb:
            kb_context = self.kb.get_context_for_question(question)
            if kb_context:
                kb_context = f"\n\n{kb_context}"
        
        try:
            resp = requests.post(f"{self.url}/api/generate", json={
                "model": self.model,
                "prompt": f"""Job application question: {question}

Profile: {context}{kb_context}

Write a brief, professional answer (1-3 sentences). Use specific examples from the experience if relevant:""",
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 150}
            }, timeout=30)
            if resp.ok:
                return resp.json().get("response", "").strip()
        except:
            pass
        return ""
    
    def choose_option(self, question: str, options: List[str], context: str) -> Optional[str]:
        if not self.available or not options:
            return None
        try:
            resp = requests.post(f"{self.url}/api/generate", json={
                "model": self.model,
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


# ═══════════════════════════════════════════════════════════════════
# SMART FILLER V3.3
# ═══════════════════════════════════════════════════════════════════

class SmartFillerV35:
    
    # V3.5: Repeatable sections configuration
    REPEATABLE_SECTIONS = {
        'work_experience': {
            'profile_key': 'work_experience',
            'add_button_text': 'Add another',
            'button_index': 0,  # First "Add another" button
            'field_patterns': {
                # selector pattern (use {N} for index) → profile field
                'company-name-{N}': 'company',
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
            'add_button_text': 'Add another',
            'button_index': 1,  # Second "Add another" button
            'field_patterns': {
                'school--{N}': 'school',
                'degree--{N}': 'degree',
                'discipline--{N}': 'discipline',
            },
            'skip_end_date_if_current': False,
        }
    }
    
    def __init__(self, headless: bool = False):
        self.headless = headless
        self.field_db = FieldDatabase()
        self.learned_db = LearnedDatabase()
        self.profile = Profile()
        self.kb = KnowledgeBase()
        self.ai = TextAI(knowledge_base=self.kb)
        
        self.playwright = None
        self.browser = None
        self.page: Page = None
        
        self.fields: List[DetectedField] = []
        self.seen_selectors: Set[str] = set()
        self.stats = {"auto": 0, "user": 0, "learned": 0, "skipped": 0, "files": 0}
    
    # ───────────────────────────────────────────────────────────────
    # BROWSER
    # ───────────────────────────────────────────────────────────────
    
    def start(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=50 if not self.headless else 0
        )
        self.page = self.browser.new_page(viewport={"width": 1400, "height": 900})
    
    def stop(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def goto(self, url: str):
        print(f"\n🌐 Opening: {url[:60]}...")
        self.page.goto(url, wait_until="networkidle")
        time.sleep(2)
        print(f"📄 Page: {self.page.title()[:50]}")
    
    def wait_for_stable(self, timeout: float = 2.0):
        prev_count = 0
        start = time.time()
        while time.time() - start < timeout:
            count = len(self.page.query_selector_all("input, select, textarea"))
            if count == prev_count:
                return
            prev_count = count
            time.sleep(0.3)
    
    def highlight(self, selector: str, color: str = "green"):
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) {{
                    el.style.outline = '3px solid {color}';
                    el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                }}
            """)
        except:
            pass
    
    def unhighlight(self, selector: str):
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) el.style.outline = '';
            """)
        except:
            pass
    
    # ───────────────────────────────────────────────────────────────
    # CASCADE DETECTION
    # ───────────────────────────────────────────────────────────────
    
    def detect_field(self, el: ElementHandle, selector: str) -> Optional[DetectedField]:
        """
        Cascade detection:
        1. HTML standard
        2. ARIA attributes
        3. Known selectors (field_database.json)
        4. Label matching
        5. Probe (last resort)
        """
        try:
            # Basic info
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            input_type = el.get_attribute("type") or "text"
            
            # Skip hidden/system
            if input_type in ("hidden", "submit", "button"):
                return None
            if not el.is_visible():
                return None
            
            # Get label
            el_id = el.get_attribute("id") or ""
            label = ""
            if el_id:
                label_el = self.page.query_selector(f"label[for='{el_id}']")
                if label_el:
                    label = label_el.inner_text().strip()
            if not label:
                label = el.get_attribute("aria-label") or \
                        el.get_attribute("placeholder") or \
                        el.get_attribute("name") or el_id
            
            # Get current value
            current = ""
            try:
                if tag == "select":
                    current = el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
                elif input_type == "checkbox":
                    current = "checked" if el.is_checked() else ""
                elif input_type != "file":
                    current = el.input_value() or ""
            except:
                pass
            
            # Required?
            required = el.get_attribute("required") is not None or \
                       el.get_attribute("aria-required") == "true"
            
            # ─────────────────────────────────────────────────────
            # LEVEL 1: HTML STANDARD
            # ─────────────────────────────────────────────────────
            
            if tag == "select":
                options = el.evaluate("e => Array.from(e.options).map(o => o.text).filter(t => t && t !== 'Select...')")
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.DROPDOWN,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    options=options, is_required=required, current_value=current
                )
            
            if tag == "textarea":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.TEXTAREA,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            if input_type == "file":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.FILE,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    is_required=required
                )
            
            if input_type == "checkbox":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.CHECKBOX,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            if input_type == "email":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.EMAIL,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            if input_type == "tel":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.PHONE,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            if input_type == "date":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.DATE,
                    detection_method="html", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            # ─────────────────────────────────────────────────────
            # LEVEL 2: ARIA ATTRIBUTES
            # ─────────────────────────────────────────────────────
            
            role = el.get_attribute("role") or ""
            aria_haspopup = el.get_attribute("aria-haspopup") or ""
            aria_autocomplete = el.get_attribute("aria-autocomplete") or ""
            
            if role == "combobox" or aria_haspopup in ("true", "listbox"):
                # This is autocomplete - type text, get suggestions
                field_type = FieldType.AUTOCOMPLETE
                return DetectedField(
                    selector=selector, label=label, field_type=field_type,
                    detection_method="aria", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            if role == "listbox":
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.DROPDOWN,
                    detection_method="aria", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current
                )
            
            # ─────────────────────────────────────────────────────
            # LEVEL 3: KNOWN SELECTORS (field_database.json)
            # ─────────────────────────────────────────────────────
            
            known = self.field_db.find_by_selector(selector)
            if known:
                profile_key = known.get("profile_key", "")
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.TEXT,
                    detection_method="known_id", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current, profile_key=profile_key
                )
            
            # ─────────────────────────────────────────────────────
            # LEVEL 4: LABEL MATCHING
            # ─────────────────────────────────────────────────────
            
            known_by_label = self.field_db.find_by_label(label)
            if known_by_label:
                profile_key = known_by_label.get("profile_key", "")
                return DetectedField(
                    selector=selector, label=label, field_type=FieldType.TEXT,
                    detection_method="label", html_tag=tag, input_type=input_type,
                    is_required=required, current_value=current, profile_key=profile_key
                )
            
            # ─────────────────────────────────────────────────────
            # LEVEL 5: DEFAULT TO TEXT (no probe!)
            # ─────────────────────────────────────────────────────
            
            return DetectedField(
                selector=selector, label=label, field_type=FieldType.TEXT,
                detection_method="default", html_tag=tag, input_type=input_type,
                is_required=required, current_value=current
            )
            
        except Exception as e:
            return None
    
    # ───────────────────────────────────────────────────────────────
    # SCAN
    # ───────────────────────────────────────────────────────────────
    
    def scan(self) -> List[DetectedField]:
        """Scan and detect all NEW fields."""
        new_fields = []
        
        for el in self.page.query_selector_all("input, select, textarea"):
            el_id = el.get_attribute("id") or ""
            el_name = el.get_attribute("name") or ""
            
            if el_id:
                selector = f"#{el_id}"
            elif el_name:
                selector = f"[name='{el_name}']"
            else:
                continue
            
            if selector in self.seen_selectors:
                continue
            
            field = self.detect_field(el, selector)
            if field:
                self.seen_selectors.add(selector)
                self.fields.append(field)
                new_fields.append(field)
        
        return new_fields
    
    # ───────────────────────────────────────────────────────────────
    # V3.4: PRE-SCAN OPTIONS
    # ───────────────────────────────────────────────────────────────
    
    def prescan_options(self, fields: List[DetectedField]) -> dict:
        """
        Pre-scan all autocomplete fields to get available options.
        Returns dict with field analysis.
        """
        print("\n🔍 Pre-scanning dropdown options...")
        results = {}
        
        for field in fields:
            if field.field_type != FieldType.AUTOCOMPLETE:
                continue
            
            try:
                el = self.page.query_selector(field.selector)
                if not el:
                    continue
                
                # Click to open dropdown
                el.click()
                time.sleep(0.4)
                
                # Wait for menu
                try:
                    self.page.wait_for_selector('.select__menu', timeout=1000)
                except:
                    self.page.keyboard.press("Escape")
                    continue
                
                # Get all options
                options = []
                opt_elements = self.page.query_selector_all('.select__option')
                for opt in opt_elements:
                    text = opt.inner_text().strip()
                    if text and text != 'No options':
                        options.append(text)
                
                # Close menu
                self.page.keyboard.press("Escape")
                time.sleep(0.1)
                
                # Classify field
                is_fixed = len(options) <= 20 and len(options) > 0
                field.options = options
                field.is_fixed = is_fixed
                
                # Find exact match from our defaults
                if is_fixed:
                    answer, _ = self.find_answer(field)
                    if answer:
                        # Find exact option that matches
                        for opt in options:
                            opt_lower = opt.lower()
                            ans_lower = answer.lower()
                            if ans_lower in opt_lower or opt_lower in ans_lower:
                                field.exact_option = opt
                                break
                
                results[field.selector] = {
                    'label': field.label[:40],
                    'options': options[:10],  # First 10 for display
                    'total': len(options),
                    'is_fixed': is_fixed,
                    'exact_match': field.exact_option or None
                }
                
                status = "FIXED" if is_fixed else "SEARCH"
                match = f" → {field.exact_option}" if field.exact_option else ""
                print(f"   [{status}] {field.label[:35]}: {len(options)} options{match}")
                
            except Exception as e:
                print(f"   ❌ {field.label[:30]}: {e}")
        
        return results
    
    def fill_with_exact_option(self, field: DetectedField) -> bool:
        """Fill autocomplete using pre-scanned exact option."""
        if not field.exact_option:
            return False
        
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return False
            
            el.click()
            time.sleep(0.3)
            
            # Wait for menu and find exact option
            self.page.wait_for_selector('.select__menu', timeout=1000)
            
            for opt in self.page.query_selector_all('.select__option'):
                if opt.inner_text().strip() == field.exact_option:
                    opt.click()
                    field.filled = True
                    return True
            
            self.page.keyboard.press("Escape")
            return False
            
        except:
            return False
    
    # ───────────────────────────────────────────────────────────────
    # FILL
    # ───────────────────────────────────────────────────────────────
    
    def upload_file(self, field: DetectedField) -> bool:
        label_lower = field.label.lower()
        if any(kw in label_lower for kw in ["resume", "cv", "attach"]):
            if RESUME_PATH.exists():
                try:
                    el = self.page.query_selector(field.selector)
                    if el:
                        el.set_input_files(str(RESUME_PATH))
                        time.sleep(0.5)
                        print(f"   📄 Uploaded: {RESUME_PATH.name}")
                        self.stats["files"] += 1
                        field.filled = True
                        return True
                except Exception as e:
                    print(f"   ❌ Upload error: {e}")
        return False
    
    # ───────────────────────────────────────────────────────────────
    # V3.5: COVER LETTER GENERATION
    # ───────────────────────────────────────────────────────────────
    
    def extract_job_info(self) -> Tuple[str, str, str]:
        """Extract job title, company, and description from page."""
        job_title = ""
        company = ""
        description = ""
        
        try:
            # Try to get from page title
            # Format: "Job Application for {TITLE} at {COMPANY}"
            title_text = self.page.title()
            
            if " at " in title_text:
                # Split by " at " to get job title and company
                parts = title_text.split(" at ", 1)  # Split only on first " at "
                job_title = parts[0].replace("Job Application for ", "").strip()
                company = parts[1].strip()
                # Clean up company name
                company = company.replace(" Careers Page", "").replace(" Careers", "").strip()
            elif " - " in title_text:
                job_title = title_text.split(" - ")[0].replace("Job Application for ", "").strip()
            
            # Try to get company from URL if not found
            if not company:
                url = self.page.url
                # Extract from greenhouse URL: ?for=coinbase
                match = re.search(r'[?&]for=([^&]+)', url)
                if match:
                    company = match.group(1).title()
            
            # Try to get company from page elements
            if not company:
                company_el = self.page.query_selector('h1, .company-name, [class*="company"]')
                if company_el:
                    company = company_el.inner_text().strip()[:50]
            
            # Try to get description
            desc_el = self.page.query_selector('.job-description, [class*="description"], .content')
            if desc_el:
                description = desc_el.inner_text()[:2000]
                
        except Exception as e:
            print(f"   ⚠️ Could not extract job info: {e}")
        
        return job_title, company, description
    
    def generate_cover_letter(self, job_title: str, company: str, description: str = "") -> Optional[Path]:
        """Generate cover letter using Ollama AI and save to file."""
        if not self.ai.available:
            print("   ⚠️ AI not available for cover letter generation")
            return None
        
        COVER_LETTERS_DIR.mkdir(exist_ok=True)
        
        # Generate filename
        safe_company = re.sub(r'[^\w\s-]', '', company)[:30].strip()
        safe_title = re.sub(r'[^\w\s-]', '', job_title)[:40].strip()
        filename = f"{safe_company}_{safe_title}_Cover_Letter.txt".replace(' ', '_')
        filepath = COVER_LETTERS_DIR / filename
        
        # Check if already exists
        if filepath.exists():
            print(f"   📄 Using existing cover letter: {filename}")
            return filepath
        
        print(f"   ✍️ Generating cover letter for {company}...")
        
        # Get profile summary
        profile_summary = self.profile.data.get("summary", "")
        if not profile_summary:
            # Build from work experience
            work = self.profile.data.get("work_experience", [])
            if work:
                profile_summary = f"{work[0].get('title', '')} with experience at {', '.join(w.get('company', '') for w in work[:3])}"
        
        # Generate with AI
        try:
            prompt = f"""Write a professional cover letter for:

POSITION: {job_title}
COMPANY: {company}
JOB DESCRIPTION: {description[:1500] if description else 'Not provided'}

CANDIDATE PROFILE:
{profile_summary}
Name: {self.profile.get('personal.first_name')} {self.profile.get('personal.last_name')}
Current Role: {self.profile.get('work_experience.0.title')} at {self.profile.get('work_experience.0.company')}

Write a concise cover letter (under 300 words) that:
1. Shows specific interest in this company and role
2. Highlights 2-3 relevant achievements with metrics
3. Has a clear call to action
4. Professional but warm tone

Format:
Dear Hiring Manager,
[content]
Sincerely,
{self.profile.get('personal.first_name')} {self.profile.get('personal.last_name')}"""

            resp = requests.post(f"{self.ai.url}/api/generate", json={
                "model": self.ai.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.7, "num_predict": 500}
            }, timeout=60)
            
            if resp.ok:
                cover_letter = resp.json().get("response", "").strip()
                
                # Save to file
                with open(filepath, 'w') as f:
                    f.write(cover_letter)
                
                print(f"   ✅ Cover letter saved: {filename}")
                return filepath
            
        except Exception as e:
            print(f"   ❌ Cover letter generation failed: {e}")
        
        return None
    
    def upload_file_v35(self, field: DetectedField) -> bool:
        """Upload file - resume or cover letter based on field label."""
        label_lower = field.label.lower()
        
        # Cover letter upload
        if any(kw in label_lower for kw in ["cover letter", "cover_letter", "coverletter"]):
            # Try to find or generate cover letter
            job_title, company, description = self.extract_job_info()
            
            if company:
                cover_path = self.generate_cover_letter(job_title, company, description)
                if cover_path and cover_path.exists():
                    try:
                        el = self.page.query_selector(field.selector)
                        if el:
                            el.set_input_files(str(cover_path))
                            time.sleep(0.5)
                            print(f"   📄 Uploaded cover letter: {cover_path.name}")
                            self.stats["files"] += 1
                            field.filled = True
                            return True
                    except Exception as e:
                        print(f"   ❌ Cover letter upload error: {e}")
            return False
        
        # Resume upload
        if any(kw in label_lower for kw in ["resume", "cv", "attach"]):
            if RESUME_PATH.exists():
                try:
                    el = self.page.query_selector(field.selector)
                    if el:
                        el.set_input_files(str(RESUME_PATH))
                        time.sleep(0.5)
                        print(f"   📄 Uploaded resume: {RESUME_PATH.name}")
                        self.stats["files"] += 1
                        field.filled = True
                        return True
                except Exception as e:
                    print(f"   ❌ Resume upload error: {e}")
        
        return False

    def fill_field(self, field: DetectedField, value: str) -> bool:
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return False
            
            el.scroll_into_view_if_needed()
            time.sleep(0.1)
            
            if field.field_type == FieldType.DROPDOWN:
                # Standard <select>
                el.select_option(label=value)
                
            elif field.field_type == FieldType.AUTOCOMPLETE:
                # React Select component - type to filter, then select BEST match
                el.click()
                time.sleep(0.2)
                
                # Type value to filter options
                el.fill("")
                el.type(value, delay=20)
                time.sleep(0.5)
                
                # Wait for filtered menu to appear
                try:
                    menu = self.page.wait_for_selector('.select__menu', timeout=1500)
                    if menu:
                        # Find BEST matching option (not just first)
                        options = self.page.query_selector_all('.select__option')
                        val_lower = value.lower()
                        
                        best_match = None
                        best_score = 0
                        
                        for opt in options:
                            opt_text = opt.inner_text().strip()
                            opt_lower = opt_text.lower()
                            
                            # Score matching:
                            # 1. Exact match = 100
                            # 2. Option starts with value = 80
                            # 3. Value at start of option = 60
                            # 4. Value contained in option = 40
                            # 5. Any partial match = 20
                            
                            score = 0
                            if opt_lower == val_lower:
                                score = 100
                            elif opt_lower.startswith(val_lower):
                                score = 80
                            elif val_lower in opt_lower and opt_lower.index(val_lower) < 10:
                                score = 60
                            elif val_lower in opt_lower:
                                score = 40
                            elif val_lower[:15] in opt_lower:
                                score = 20
                            
                            if score > best_score:
                                best_score = score
                                best_match = opt
                        
                        if best_match:
                            best_match.click()
                        elif options:
                            options[0].click()  # Fallback to first
                        
                        time.sleep(0.2)
                except:
                    # Fallback: keyboard
                    self.page.keyboard.press("ArrowDown")
                    time.sleep(0.1)
                    self.page.keyboard.press("Enter")
                
                time.sleep(0.2)
                
            elif field.field_type == FieldType.CHECKBOX:
                should_check = value.lower() in ("yes", "true", "1", "checked")
                if should_check != el.is_checked():
                    el.click()
                    
            else:
                # Text, email, phone, textarea
                el.fill(value)
            
            field.filled = True
            return True
            
        except Exception as e:
            print(f"   ❌ Fill error: {e}")
            return False
    
    def read_field(self, field: DetectedField) -> str:
        try:
            el = self.page.query_selector(field.selector)
            if not el:
                return ""
            if field.html_tag == "select":
                return el.evaluate("e => e.options[e.selectedIndex]?.text || ''")
            elif field.field_type == FieldType.CHECKBOX:
                return "checked" if el.is_checked() else ""
            elif field.field_type == FieldType.AUTOCOMPLETE:
                # React Select stores value in .select__single-value
                # Use JavaScript to find it relative to the input
                result = el.evaluate("""
                    e => {
                        // Go up to .select__control, then to parent, find .select__single-value
                        const control = e.closest('.select__control');
                        if (control && control.parentElement) {
                            const sv = control.parentElement.querySelector('.select__single-value');
                            if (sv) return sv.textContent.trim();
                        }
                        return '';
                    }
                """)
                return result or el.input_value() or ""
            else:
                return el.input_value() or ""
        except:
            return ""
    
    # ───────────────────────────────────────────────────────────────
    # FIND ANSWER
    # ───────────────────────────────────────────────────────────────
    
    def find_answer(self, field: DetectedField) -> Tuple[Optional[str], str]:
        """Find answer. Returns (value, source)"""
        label = field.label
        
        # 1. Learned database
        if field.field_type in (FieldType.DROPDOWN, FieldType.AUTOCOMPLETE):
            saved = self.learned_db.find_dropdown_choice(label)
            if saved:
                return saved, "learned"
        else:
            saved = self.learned_db.find_answer(label)
            if saved:
                return saved, "learned"
        
        # 2. Profile key (if set by detection)
        if field.profile_key:
            val = self.profile.get(field.profile_key)
            if val:
                return val, "profile"
        
        # 3. Yes/No defaults BEFORE profile label (to avoid 'country' matching 'authorized to work in country')
        yn = self.profile.find_yes_no(label)
        if yn:
            return yn, "default"
        
        # 4. Profile by label
        val, _ = self.profile.find_by_label(label)
        if val:
            return val, "profile"
        
        # 5. Demographic defaults
        demo = self.profile.find_demographic(label)
        if demo:
            return demo, "default"
        
        # 6. Text defaults (years experience, how heard, etc)
        text_default = self.profile.find_text_default(label)
        if text_default:
            return text_default, "default"
        
        return None, ""
    
    # ───────────────────────────────────────────────────────────────
    # PROCESS
    # ───────────────────────────────────────────────────────────────
    
    def process_fields(self, fields: List[DetectedField], interactive: bool = True) -> List[DetectedField]:
        """Process fields. Returns those needing review."""
        needs_review = []
        current_role_checked = False
        
        # First pass: check if "Current role" checkbox exists and should be checked
        for field in fields:
            ll = field.label.lower()
            if field.field_type == FieldType.CHECKBOX and any(x in ll for x in ["current role", "currently work", "i currently work"]):
                current_role_checked = True
                break
        
        for field in fields:
            # File upload
            if field.field_type == FieldType.FILE:
                self.highlight(field.selector, "blue")
                self.upload_file_v35(field)
                self.unhighlight(field.selector)
                continue
            
            # Skip end date if current role is checked
            ll = field.label.lower()
            if current_role_checked and ("end date" in ll or "end month" in ll or "end year" in ll):
                print(f"   ⏭️  {field.label[:35]:<35} (skipped - current role)")
                continue
            
            # Skip filled
            if field.current_value and field.current_value not in ("", "Select...", "Select"):
                continue
            
            # V3.4: For FIXED autocomplete with exact_option - use direct click
            if field.field_type == FieldType.AUTOCOMPLETE and field.is_fixed and field.exact_option:
                self.highlight(field.selector, "green")
                if self.fill_with_exact_option(field):
                    field.answer = field.exact_option
                    field.answer_source = "prescan"
                    self.stats["auto"] += 1
                    print(f"   ✅ {field.label[:35]:<35} = {field.exact_option[:20]:<20} (exact) [prescan]")
                    self.unhighlight(field.selector)
                    continue
                self.unhighlight(field.selector)
            
            # Find answer
            answer, source = self.find_answer(field)
            
            if answer:
                self.highlight(field.selector, "green")
                self.fill_field(field, answer)
                self.unhighlight(field.selector)
                
                field.answer = answer
                field.answer_source = source
                self.stats["auto"] += 1
                
                method = f"[{field.detection_method}]"
                print(f"   ✅ {field.label[:35]:<35} = {answer[:20]:<20} ({source}) {method}")
            else:
                needs_review.append(field)
        
        return needs_review
    
    def interactive_review(self, fields: List[DetectedField]):
        if not fields:
            return
        
        print("\n" + "="*65)
        print(f"🎓 REVIEW: {len(fields)} fields need input")
        print("="*65)
        
        for field in fields:
            self.highlight(field.selector, "orange")
            
            print(f"\n📌 {field.label[:60]}")
            print(f"   Type: {field.field_type.value} (detected by: {field.detection_method})")
            
            if field.field_type in (FieldType.DROPDOWN, FieldType.AUTOCOMPLETE):
                # For autocomplete - AI suggests, user types
                if self.ai.available:
                    suggested = self.ai.generate(field.label, self.profile.get_context())
                    if suggested:
                        print(f"   💡 AI suggests: {suggested[:50]}")
                        self.fill_field(field, suggested)
                
                print(f"\n   Edit in browser if needed, then ENTER (or 's' to skip):")
                user = input("   > ").strip()
                
                if user.lower() == 's':
                    self.stats["skipped"] += 1
                else:
                    final = self.read_field(field)
                    if final and final not in ("Select...", ""):
                        self.learned_db.save_dropdown_choice(field.label, final)
                        self.stats["learned"] += 1
                        self.stats["user"] += 1
                    else:
                        self.stats["skipped"] += 1
            else:
                # Text field
                if self.ai.available:
                    suggested = self.ai.generate(field.label, self.profile.get_context())
                    if suggested:
                        print(f"   💡 AI suggests: {suggested[:50]}")
                        self.fill_field(field, suggested)
                
                print(f"\n   Edit in browser if needed, then ENTER (or 's' to skip):")
                user = input("   > ").strip()
                
                if user.lower() == 's':
                    self.stats["skipped"] += 1
                else:
                    final = self.read_field(field)
                    if final:
                        self.learned_db.save_answer(field.label, final)
                        self.stats["learned"] += 1
                        self.stats["user"] += 1
                    else:
                        self.stats["skipped"] += 1
            
            self.unhighlight(field.selector)
    
    # ───────────────────────────────────────────────────────────────
    # MULTI-PASS
    # ───────────────────────────────────────────────────────────────
    
    # ───────────────────────────────────────────────────────────────
    # V3.5: REPEATABLE SECTIONS
    # ───────────────────────────────────────────────────────────────
    
    def fill_section_entry(self, section_name: str, entry_index: int, form_index: int) -> bool:
        """
        Fill one entry of a repeatable section.
        
        Args:
            section_name: 'work_experience' or 'education'
            entry_index: Index in profile data (0, 1, 2...)
            form_index: Index in form fields (0, 1, 2...)
        
        Returns:
            True if any field was filled
        """
        config = self.REPEATABLE_SECTIONS.get(section_name)
        if not config:
            return False
        
        profile_key = config['profile_key']
        field_patterns = config['field_patterns']
        skip_end_if_current = config.get('skip_end_date_if_current', False)
        
        # Get entry data from profile
        entry_data = self.profile.data.get(profile_key, [])
        if entry_index >= len(entry_data):
            return False
        
        entry = entry_data[entry_index]
        is_current = entry.get('current', False)
        
        filled_any = False
        
        print(f"\n   📝 {section_name}[{entry_index}]: {entry.get('company', entry.get('school', ''))[:40]}")
        
        for pattern, field_name in field_patterns.items():
            # Build selector for this form index
            selector = '#' + pattern.replace('{N}', str(form_index))
            
            # Skip end date fields if current role
            if skip_end_if_current and is_current and 'end' in field_name:
                continue
            
            # Get value from entry
            value = entry.get(field_name, '')
            if not value:
                continue
            
            # Handle boolean current field
            if field_name == 'current' and value == True:
                value = 'checked'
            elif field_name == 'current' and value == False:
                continue  # Don't check if not current
            
            # Find element
            el = self.page.query_selector(selector)
            if not el:
                continue
            
            # Create temporary field object
            el_type = el.get_attribute('type') or 'text'
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            
            # Detect field type
            role = el.get_attribute('role') or ''
            aria_haspopup = el.get_attribute('aria-haspopup') or ''
            
            if tag == 'select':
                field_type = FieldType.DROPDOWN
            elif role == 'combobox' or aria_haspopup in ('true', 'listbox'):
                field_type = FieldType.AUTOCOMPLETE
            elif el_type == 'checkbox':
                field_type = FieldType.CHECKBOX
            else:
                field_type = FieldType.TEXT
            
            temp_field = DetectedField(
                selector=selector,
                label=field_name,
                field_type=field_type,
                detection_method='repeatable',
                html_tag=tag,
                input_type=el_type
            )
            
            # Fill the field
            if self.fill_field(temp_field, str(value)):
                filled_any = True
                print(f"      ✅ {field_name}: {str(value)[:25]}")
            
            time.sleep(0.1)
        
        return filled_any
    
    def click_add_another(self, section_name: str) -> bool:
        """Click 'Add another' button for a section."""
        config = self.REPEATABLE_SECTIONS.get(section_name)
        if not config:
            return False
        
        button_index = config['button_index']
        
        add_buttons = self.page.query_selector_all('button.add-another-button')
        if button_index < len(add_buttons):
            add_buttons[button_index].click()
            time.sleep(1)  # Wait for new fields to appear
            self.wait_for_stable()
            return True
        
        return False
    
    def fill_repeatable_section(self, section_name: str):
        """Fill all entries for a repeatable section."""
        config = self.REPEATABLE_SECTIONS.get(section_name)
        if not config:
            return
        
        profile_key = config['profile_key']
        entries = self.profile.data.get(profile_key, [])
        
        if not entries:
            print(f"\n⚠️ No {section_name} entries in profile")
            return
        
        print(f"\n🔄 Filling {section_name}: {len(entries)} entries")
        
        # Fill first entry (index 0 in form)
        self.fill_section_entry(section_name, entry_index=0, form_index=0)
        
        # Add and fill additional entries
        for i in range(1, len(entries)):
            print(f"\n   ➕ Adding entry {i+1}...")
            if self.click_add_another(section_name):
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

    def multi_pass_fill(self, interactive: bool = True, max_passes: int = 5):
        for pass_num in range(1, max_passes + 1):
            print(f"\n🔄 Pass {pass_num}...")
            
            self.wait_for_stable()
            new_fields = self.scan()
            
            if not new_fields:
                print("   No new fields.")
                break
            
            # Stats by detection method
            methods = {}
            for f in new_fields:
                methods[f.detection_method] = methods.get(f.detection_method, 0) + 1
            print(f"   Found {len(new_fields)} fields: {methods}")
            
            # V3.4: Pre-scan autocomplete options BEFORE filling
            autocomplete_count = sum(1 for f in new_fields if f.field_type == FieldType.AUTOCOMPLETE)
            if autocomplete_count > 0:
                self.prescan_options(new_fields)
            
            # Now fill with pre-scanned data
            print("\n📝 Filling fields...")
            needs_review = self.process_fields(new_fields, interactive)
            
            if interactive and needs_review:
                self.interactive_review(needs_review)
            
            time.sleep(0.5)
        
        self.final_report()
    
    # ───────────────────────────────────────────────────────────────
    # FINAL REPORT
    # ───────────────────────────────────────────────────────────────
    
    def final_report(self):
        print("\n" + "="*65)
        print("📋 FINAL REPORT")
        print("="*65)
        
        filled = []
        empty = []
        errors = []
        
        for field in self.fields:
            if field.field_type == FieldType.FILE:
                if field.filled:
                    filled.append(f"📄 {field.label[:40]}")
                continue
            
            value = self.read_field(field)
            if value and value not in ("Select...", "Select", ""):
                filled.append(f"{field.label[:35]}: {value[:20]}")
            else:
                empty.append(field.label[:50])
            
            try:
                el = self.page.query_selector(field.selector)
                if el and el.get_attribute("aria-invalid") == "true":
                    errors.append(field.label[:50])
            except:
                pass
        
        print(f"\n✅ Filled ({len(filled)}):")
        for f in filled[:20]:
            print(f"   {f}")
        if len(filled) > 20:
            print(f"   ... and {len(filled)-20} more")
        
        if empty:
            print(f"\n❌ Empty ({len(empty)}):")
            for e in empty:
                print(f"   {e}")
        
        if errors:
            print(f"\n⚠️ Errors ({len(errors)}):")
            for e in errors:
                print(f"   {e}")
        
        print("\n" + "="*65)
        print("📊 STATS")
        print("="*65)
        print(f"   Auto-filled: {self.stats['auto']}")
        print(f"   User filled: {self.stats['user']}")
        print(f"   Files:       {self.stats['files']}")
        print(f"   Learned:     {self.stats['learned']}")
        print(f"   Skipped:     {self.stats['skipped']}")
        print("="*65)
    
    # ───────────────────────────────────────────────────────────────
    # MAIN
    # ───────────────────────────────────────────────────────────────
    
    def run(self, url: str, interactive: bool = True):
        try:
            self.start()
            self.goto(url)
            self.multi_pass_fill(interactive)
            
            if not self.headless:
                print("\n👀 Review in browser. Submit when ready.")
                print("   Press ENTER to close...")
                input()
        finally:
            self.stop()


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*65)
    print("🚀 SMART FILLER V3.4 - Pre-Scan Strategy")
    print("   1. Scan all fields")
    print("   2. Pre-scan dropdown options")
    print("   3. Find exact matches")
    print("   4. Fill with confidence")
    print("="*65)
    
    filler = SmartFillerV35(headless=False)
    filler.run(url, interactive=True)


if __name__ == "__main__":
    main()
