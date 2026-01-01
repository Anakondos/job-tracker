"""
Smart Filler V3.3 - Cascade Field Detection

Detection cascade (most reliable first):
1. HTML standard (select, textarea, type=file/checkbox/email/tel)
2. ARIA attributes (role=combobox, aria-haspopup)
3. Known selectors (field_database.json)
4. Label matching (fuzzy)
5. Probe (click and check) - last resort

Plus all V3.2 features:
- File upload
- Multi-pass for dynamic fields
- Wait for JS/React
- Final verification
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

class SmartFillerV33:
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
                # React Select component - type to filter, then select
                el.click()
                time.sleep(0.2)
                
                # Type value to filter options
                el.fill("")
                el.type(value, delay=20)
                time.sleep(0.4)
                
                # Wait for filtered menu to appear
                try:
                    menu = self.page.wait_for_selector('.select__menu', timeout=1500)
                    if menu:
                        # Find matching option
                        options = self.page.query_selector_all('.select__option')
                        clicked = False
                        
                        for opt in options:
                            opt_text = opt.inner_text().lower()
                            val_lower = value.lower()
                            
                            # Match if option contains our value or vice versa
                            if val_lower[:10] in opt_text or opt_text in val_lower:
                                opt.click()
                                clicked = True
                                break
                        
                        if not clicked and options:
                            # Click first filtered option
                            options[0].click()
                        
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
                self.upload_file(field)
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
    print("🚀 SMART FILLER V3.3 - Cascade Detection")
    print("   1. HTML standard")
    print("   2. ARIA attributes")
    print("   3. Known selectors")
    print("   4. Label matching")
    print("="*65)
    
    filler = SmartFillerV33(headless=False)
    filler.run(url, interactive=True)


if __name__ == "__main__":
    main()
