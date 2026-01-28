"""
V6 Form Filler - Simple, Sequential, Working

Principles:
1. Fill fields in visual order (top to bottom)
2. Each field type has ONE proven method
3. Learn from user corrections
4. Universal - works on any form, remembers specifics
5. AI fallback for unknown questions
"""

from playwright.sync_api import sync_playwright, Page, Frame, ElementHandle
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import json
import time
import os

# AI Helper
try:
    from .ai_helper import AIHelper
except ImportError:
    AIHelper = None

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────

PROFILE_PATH = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/job-tracker-dev/data/profile.json")
ANSWERS_DB_PATH = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/job-tracker-dev/data/answer_library.json")

CV_PATH = "/Users/anton/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/CV_Anton_Kondakov_Product Manager.pdf"
COVER_LETTER_PATH = "/Users/anton/Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/Cover_Letter_Anton_Kondakov_ProductM.docx"

# ─────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Profile:
    """User profile data"""
    first_name: str = "Anton"
    last_name: str = "Kondakov"
    email: str = "anton.kondakov.PM@gmail.com"
    phone: str = "9105360602"
    location: str = "Wake Forest"
    linkedin: str = "https://linkedin.com/in/antonkondakov"
    
    # Work Experience
    work_experience: List[Dict] = field(default_factory=list)
    
    # Education  
    education: List[Dict] = field(default_factory=list)
    
    # EEO
    gender: str = "Male"
    hispanic: str = "No"
    race: str = "White"
    veteran: str = "I am not a protected veteran"
    disability: str = "No, I do not have a disability"
    
    # Defaults for common questions
    work_authorized: str = "Yes"
    sponsorship_required: str = "No"
    age_18_plus: str = "Yes"
    
    @classmethod
    def load(cls, path: str) -> 'Profile':
        """Load profile from JSON"""
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            
            profile = cls()
            profile.first_name = data.get('first_name', profile.first_name)
            profile.last_name = data.get('last_name', profile.last_name)
            profile.email = data.get('email', profile.email)
            profile.phone = data.get('phone', profile.phone)
            profile.location = data.get('location', profile.location)
            profile.linkedin = data.get('linkedin', profile.linkedin)
            profile.work_experience = data.get('work_experience', [])
            profile.education = data.get('education', [])
            
            return profile
        except:
            return cls()


class AnswerDB:
    """Learned answers database"""
    
    def __init__(self, path: str):
        self.path = path
        self.answers: Dict[str, str] = {}
        self.load()
    
    def load(self):
        try:
            with open(self.path, 'r') as f:
                self.answers = json.load(f)
        except:
            self.answers = {}
    
    def save(self):
        with open(self.path, 'w') as f:
            json.dump(self.answers, f, indent=2)
    
    def get(self, label: str) -> Optional[str]:
        """Get answer by label (searches nested structure)"""
        label_lower = label.lower()
        
        def search_dict(d, path=""):
            """Recursively search dict for matching key"""
            if not isinstance(d, dict):
                return None
            
            for key, value in d.items():
                key_lower = key.lower()
                
                # Check if key matches label
                if key_lower in label_lower or label_lower in key_lower:
                    # Return only simple values
                    if isinstance(value, str):
                        return value
                    elif isinstance(value, bool):
                        return "Yes" if value else "No"
                    elif isinstance(value, (int, float)):
                        return str(value)
                
                # Recursively search nested dicts (but not lists)
                if isinstance(value, dict):
                    result = search_dict(value, f"{path}.{key}")
                    if result:
                        return result
            
            return None
        
        return search_dict(self.answers)
    
    def set(self, label: str, answer: str):
        """Save answer"""
        self.answers[label] = answer
        self.save()


# ─────────────────────────────────────────────────────────────────────
# V6 FORM FILLER
# ─────────────────────────────────────────────────────────────────────

class FormFillerV6:
    """Simple, sequential form filler with AI fallback"""
    
    def __init__(self):
        self.page: Optional[Page] = None
        self.frame: Optional[Frame] = None
        self.profile = Profile.load(PROFILE_PATH)
        self.answers_db = AnswerDB(ANSWERS_DB_PATH)
        
        # AI Helper
        self.ai = AIHelper() if AIHelper else None
        if self.ai and self.ai.available:
            print("🤖 AI Helper: enabled")
        else:
            print("⚠️ AI Helper: disabled")
    
    def connect(self, port: int = 9222):
        """Connect to Chrome via CDP"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
        self.context = self.browser.contexts[0]
        
        # Find page with job form (greenhouse, lever, etc.)
        self.page = None
        for pg in self.context.pages:
            if any(ats in pg.url for ats in ['greenhouse', 'lever', 'ashby', 'workday', 'coinbase']):
                self.page = pg
                break
        
        if not self.page:
            self.page = self.context.pages[0]
        
        print(f"✅ Connected to Chrome")
        print(f"   URL: {self.page.url[:60]}")
    
    def find_frame(self) -> bool:
        """Find application iframe (Greenhouse, Lever, etc.)"""
        # Wait for page to load
        time.sleep(2)
        
        # Method 1: Find by URL
        for f in self.page.frames:
            if any(ats in f.url for ats in ['greenhouse', 'lever', 'ashby', 'workday']):
                self.frame = f
                print(f"✅ Found ATS iframe (by URL)")
                return True
        
        # Method 2: Find by form content (for iframes with empty URL)
        for f in self.page.frames:
            if f.query_selector('#first_name') and f.query_selector('#last_name'):
                self.frame = f
                print(f"✅ Found ATS iframe (by form fields)")
                # Scroll to form if it's far down the page
                fn = f.query_selector('#first_name')
                if fn:
                    box = fn.bounding_box()
                    if box and box['y'] > 1000:
                        print(f"   Scrolling to form (y={int(box['y'])})...")
                        self.page.evaluate(f"window.scrollTo(0, {int(box['y']) - 200})")
                        time.sleep(0.5)
                return True
        
        # No iframe - use main page
        print(f"⚠️ No ATS iframe found, using main page")
        self.frame = self.page.main_frame
        return True
    
    # ─────────────────────────────────────────────────────────────────
    # CORE FILL METHODS - Each field type has ONE proven method
    # ─────────────────────────────────────────────────────────────────
    
    def fill_text(self, selector: str, value: str, name: str = "") -> bool:
        """Fill text input using fill() method"""
        el = self.frame.query_selector(selector)
        if not el:
            print(f"    ⚠️ {name}: not found")
            return False
        
        el.scroll_into_view_if_needed()
        el.fill(value)
        el.evaluate('e => e.blur()')
        print(f"    ✅ {name}: {value[:25]}")
        return True
    
    def fill_phone(self, selector: str, value: str, name: str = "Phone") -> bool:
        """Fill phone using keyboard.type() with delay"""
        el = self.frame.query_selector(selector)
        if not el:
            print(f"    ⚠️ {name}: not found")
            return False
        
        el.scroll_into_view_if_needed()
        el.click()
        time.sleep(0.2)
        self.page.keyboard.type(value, delay=30)
        el.evaluate('e => e.blur()')
        print(f"    ✅ {name}: {value}")
        return True
    
    def fill_dropdown(self, selector: str, answer: str, name: str = "") -> bool:
        """
        Fill dropdown using aria-controls method.
        
        PROVEN METHOD:
        1. Escape - close any open dropdowns
        2. Click dropdown to open
        3. Use aria-controls to find CORRECT listbox
        4. Find option containing answer text
        5. Click option
        """
        el = self.frame.query_selector(selector)
        if not el:
            print(f"    ⚠️ {name}: not found")
            return False
        
        # Close any open dropdowns
        self.page.keyboard.press('Escape')
        time.sleep(0.1)
        
        # Scroll and click
        el.scroll_into_view_if_needed()
        time.sleep(0.1)
        el.click()
        time.sleep(0.4)
        
        # Get listbox via aria-controls
        controls_id = el.get_attribute('aria-controls')
        if not controls_id:
            self.page.keyboard.press('Escape')
            print(f"    ⚠️ {name}: no aria-controls")
            return False
        
        listbox = self.frame.query_selector(f'#{controls_id}')
        if not listbox:
            self.page.keyboard.press('Escape')
            print(f"    ⚠️ {name}: listbox not found")
            return False
        
        options = listbox.query_selector_all('[role="option"]')
        if not options:
            self.page.keyboard.press('Escape')
            print(f"    ⚠️ {name}: no options")
            return False
        
        # Find matching option
        answer_lower = answer.lower()
        for opt in options:
            opt_text = opt.inner_text().strip()
            if answer_lower in opt_text.lower():
                opt.click()
                time.sleep(0.2)
                print(f"    ✅ {name}: {opt_text[:30]}")
                return True
        
        # No match - close
        self.page.keyboard.press('Escape')
        print(f"    ⚠️ {name}: '{answer}' not in options")
        return False
    
    def fill_location(self, selector: str, value: str, name: str = "Location") -> bool:
        """
        Fill location autocomplete.
        
        PROVEN METHOD:
        1. Click field
        2. Type location text
        3. Wait 2 SECONDS for API response
        4. Select first option from dropdown
        """
        el = self.frame.query_selector(selector)
        if not el:
            print(f"    ⚠️ {name}: not found")
            return False
        
        self.page.keyboard.press('Escape')
        time.sleep(0.1)
        
        el.scroll_into_view_if_needed()
        el.click()
        time.sleep(0.3)
        
        # Type location
        self.page.keyboard.type(value, delay=30)
        
        # CRITICAL: Wait for API
        print(f"    ⏳ Waiting for location API...")
        time.sleep(2)
        
        # Get options via aria-controls
        controls_id = el.get_attribute('aria-controls')
        if controls_id:
            listbox = self.frame.query_selector(f'#{controls_id}')
            if listbox:
                options = listbox.query_selector_all('[role="option"]')
                if options:
                    selected = options[0].inner_text()[:40]
                    options[0].click()
                    time.sleep(0.2)
                    print(f"    ✅ {name}: {selected}")
                    return True
        
        # No options - just blur
        el.evaluate('e => e.blur()')
        print(f"    ⚠️ {name}: typed but no autocomplete")
        return True
    
    def fill_school_search(self, selector: str, search: str, fallback: str = "0 - Other", name: str = "School") -> bool:
        """
        Fill searchable school dropdown.
        
        PROVEN METHOD:
        1. Click dropdown
        2. Type search text  
        3. Wait for results
        4. If good match found - select it
        5. Otherwise use fallback "0 - Other" or "MIPT"
        """
        el = self.frame.query_selector(selector)
        if not el:
            print(f"    ⚠️ {name}: not found")
            return False
        
        self.page.keyboard.press('Escape')
        time.sleep(0.1)
        
        el.scroll_into_view_if_needed()
        el.click()
        time.sleep(0.3)
        
        # Type search - use shorter search for better matches
        search_short = search[:30] if len(search) > 30 else search
        self.page.keyboard.type(search_short, delay=20)
        time.sleep(1.0)  # Wait longer for search API
        
        # Check options
        controls_id = el.get_attribute('aria-controls')
        if controls_id:
            listbox = self.frame.query_selector(f'#{controls_id}')
            if listbox:
                options = listbox.query_selector_all('[role="option"]')
                if options:
                    # Simply select first option if search found results
                    first_opt = options[0].inner_text().strip()
                    
                    # Check if first option looks like our search (not "No results")
                    if 'no result' not in first_opt.lower() and 'no option' not in first_opt.lower():
                        options[0].click()
                        print(f"    ✅ {name}: {first_opt[:35]}")
                        time.sleep(0.2)
                        return True
        
        # No results from search - try fallback
        self.page.keyboard.press('Escape')
        time.sleep(0.1)
        el.click()
        time.sleep(0.2)
        
        # Clear and type fallback
        self.page.keyboard.press('Control+a')
        self.page.keyboard.press('Backspace')
        self.page.keyboard.type(fallback, delay=20)
        time.sleep(1.0)
        
        if controls_id:
            listbox = self.frame.query_selector(f'#{controls_id}')
            if listbox:
                options = listbox.query_selector_all('[role="option"]')
                if options:
                    options[0].click()
                    print(f"    ✅ {name}: {options[0].inner_text()[:35]} (fallback)")
                    time.sleep(0.2)
                    return True
        
        self.page.keyboard.press('Escape')
        print(f"    ⚠️ {name}: failed")
        return False
    
    def fill_file(self, selector: str, path: str, name: str = "File") -> bool:
        """Upload file"""
        el = self.frame.query_selector(selector)
        if not el:
            print(f"    ⚠️ {name}: not found")
            return False
        
        el.set_input_files(path)
        time.sleep(1)
        print(f"    ✅ {name}: {os.path.basename(path)[:30]}")
        return True
    
    def fill_checkbox(self, selector: str, check: bool = True, name: str = "") -> bool:
        """Fill checkbox"""
        el = self.frame.query_selector(selector)
        if not el:
            return False
        
        if el.is_checked() != check:
            el.click()
        print(f"    ✅ {name}: {'checked' if check else 'unchecked'}")
        return True
    
    def click_add_another(self, section: str = "work") -> bool:
        """Click 'Add another' button for a section"""
        buttons = self.frame.query_selector_all('button:has-text("Add another")')
        
        # Work experience is usually first, education second
        if section == "work" and len(buttons) >= 1:
            buttons[0].click()
            time.sleep(0.5)
            return True
        elif section == "education" and len(buttons) >= 2:
            buttons[1].click()
            time.sleep(0.5)
            return True
        elif buttons:
            buttons[-1].click()
            time.sleep(0.5)
            return True
        
        return False
    
    # ─────────────────────────────────────────────────────────────────
    # GREENHOUSE FORM FILL - In visual order
    # ─────────────────────────────────────────────────────────────────
    
    def fill_greenhouse(self):
        """Fill Greenhouse form in visual order (top to bottom)"""
        
        print("\n" + "=" * 60)
        print("V6 GREENHOUSE FORM FILL")
        print("=" * 60)
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 1: BASIC INFO
        # ─────────────────────────────────────────────────────────────
        print("\n[1] BASIC INFO")
        self.fill_text('#first_name', self.profile.first_name, 'First Name')
        self.fill_text('#last_name', self.profile.last_name, 'Last Name')
        self.fill_text('#preferred_name', self.profile.first_name, 'Preferred Name')  # Same as first name
        self.fill_text('#email', self.profile.email, 'Email')
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 2: COUNTRY
        # ─────────────────────────────────────────────────────────────
        print("\n[2] COUNTRY")
        self.fill_dropdown('#country', 'United States', 'Country')
        time.sleep(0.3)
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 3: PHONE
        # ─────────────────────────────────────────────────────────────
        print("\n[3] PHONE")
        self.fill_phone('#phone', self.profile.phone, 'Phone')
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 4: LOCATION
        # ─────────────────────────────────────────────────────────────
        print("\n[4] LOCATION")
        self.fill_location('#candidate-location', self.profile.location, 'Location')
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 5: FILES
        # ─────────────────────────────────────────────────────────────
        print("\n[5] FILES")
        files = self.frame.query_selector_all('input[type="file"]')
        if len(files) >= 1:
            self.fill_file('input[type="file"]:nth-of-type(1)', CV_PATH, 'Resume')
        if len(files) >= 2:
            files[1].set_input_files(COVER_LETTER_PATH)
            print(f"    ✅ Cover Letter")
            time.sleep(1)
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 6: WORK EXPERIENCE
        # ─────────────────────────────────────────────────────────────
        print("\n[6] WORK EXPERIENCE")
        
        work_exp = self.profile.work_experience or [
            {"company": "DXC Technology - London Market", "title": "Senior Delivery Manager", 
             "start_month": "July", "start_year": "2025", "current": True},
            {"company": "Luxoft (DXC) / Deutsche Bank", "title": "Senior Technical Program Manager",
             "start_month": "February", "start_year": "2020", "end_month": "June", "end_year": "2025"},
            {"company": "Luxoft Poland / UBS", "title": "Technical Program Manager",
             "start_month": "January", "start_year": "2016", "end_month": "January", "end_year": "2020"},
        ]
        
        for i, exp in enumerate(work_exp):
            if i > 0:
                self.click_add_another("work")
                print(f"\n    --- Entry {i+1} ---")
            
            self.fill_text(f'#company-name-{i}', exp['company'], 'Company')
            self.fill_text(f'#title-{i}', exp['title'], 'Title')
            self.fill_dropdown(f'#start-date-month-{i}', exp['start_month'], 'Start Month')
            self.fill_text(f'#start-date-year-{i}', exp['start_year'], 'Start Year')
            
            if exp.get('current'):
                self.fill_checkbox(f'#current-role-{i}_1', True, 'Current Role')
            else:
                self.fill_dropdown(f'#end-date-month-{i}', exp.get('end_month', ''), 'End Month')
                self.fill_text(f'#end-date-year-{i}', exp.get('end_year', ''), 'End Year')
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 7: EDUCATION
        # ─────────────────────────────────────────────────────────────
        print("\n[7] EDUCATION")
        
        education = self.profile.education or [
            {"school": "Moscow Institute of Physics", "school_fallback": "MIPT", "degree": "Master's Degree", "discipline": "Computer Science"},
            {"school": "International Institute of Management LINK", "school_fallback": "0 - Other", "degree": "Master of Business", "discipline": "Business"},
        ]
        
        for i, edu in enumerate(education):
            if i > 0:
                self.click_add_another("education")
                print(f"\n    --- Entry {i+1} ---")
            
            # School - searchable with fallback
            self.fill_school_search(
                f'#school--{i}', 
                edu['school'], 
                edu.get('school_fallback', '0 - Other'),
                'School'
            )
            time.sleep(0.3)  # Wait for school dropdown to close
            self.fill_dropdown(f'#degree--{i}', edu['degree'], 'Degree')
            self.fill_dropdown(f'#discipline--{i}', edu['discipline'], 'Discipline')
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 8: LINKEDIN
        # ─────────────────────────────────────────────────────────────
        print("\n[8] LINKEDIN")
        # Find LinkedIn field (ID varies per form)
        linkedin_field = self.frame.query_selector('[id*="linkedin" i], [name*="linkedin" i]')
        if linkedin_field:
            lid = linkedin_field.get_attribute('id')
            self.fill_text(f'#{lid}', self.profile.linkedin, 'LinkedIn')
        else:
            # Try by label
            labels = self.frame.query_selector_all('label')
            for l in labels:
                if 'linkedin' in l.inner_text().lower():
                    for_id = l.get_attribute('for')
                    if for_id:
                        self.fill_text(f'#{for_id}', self.profile.linkedin, 'LinkedIn')
                        break
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 9: CUSTOM QUESTIONS (scan and fill)
        # ─────────────────────────────────────────────────────────────
        print("\n[9] CUSTOM QUESTIONS")
        self._fill_custom_questions()
        
        # ─────────────────────────────────────────────────────────────
        # SECTION 10: EEO
        # ─────────────────────────────────────────────────────────────
        print("\n[10] EEO")
        self.fill_dropdown('#gender', self.profile.gender, 'Gender')
        self.fill_dropdown('#hispanic_ethnicity', self.profile.hispanic, 'Hispanic')
        time.sleep(0.3)  # Wait for Race to appear
        self.fill_dropdown('#race', self.profile.race, 'Race')
        self.fill_dropdown('#veteran_status', self.profile.veteran, 'Veteran')
        self.fill_dropdown('#disability_status', self.profile.disability, 'Disability')
        
        # ─────────────────────────────────────────────────────────────
        # FINAL CHECK
        # ─────────────────────────────────────────────────────────────
        self._final_check()
    
    def _fill_custom_questions(self):
        """Scan and fill custom questions based on label matching"""
        
        # Find all question fields
        questions = self.frame.query_selector_all('[id^="question_"]')
        
        # Default answers based on common question patterns
        # Order matters! More specific patterns first
        defaults = [
            # Government/Compliance
            ('current government official', 'No'),
            ('government official', 'No'),
            ('relative of a government', 'No'),
            ('conflict of interest', 'No'),
            
            # Employment history
            ('previously worked', 'No'),
            ('previously employed', 'No'),
            ('currently an employee', 'No'),
            ('employed by', 'No'),
            ('referred', 'No'),
            
            # Work authorization  
            ('eligible to work', 'Yes'),
            ('require sponsorship', 'No'),
            ('need.*sponsor', 'No'),
            ('sponsor', 'No'),
            ('visa', 'No'),
            ('legally authorized', 'Yes'),
            ('authorized to work', 'Yes'),
            
            # Age/Basic
            ('at least 18', 'Yes'),
            ('18 years', 'Yes'),
            
            # Technical/Experience questions
            ('minimum of', 'Yes'),  # "Do you hold a minimum of X years"
            ('technical product', 'Yes'),  # Technical PM experience
            ('years of experience', 'Yes'),
            
            # Source
            ('hear about', 'LinkedIn'),
            ('how did you', 'LinkedIn'),
            
            # Privacy/Consent
            ('privacy', 'Confirmed'),
            ('confirm receipt', 'Confirmed'),
            ('ai tool', 'Yes'),
            ('understand that', 'Yes'),
            
            # Language preference
            ('preferred la', 'English'),  # Preferred Language
        ]
        
        for q in questions:
            qid = q.get_attribute('id')
            if not qid:
                continue
            
            # Skip label/description elements (only process main question fields)
            if qid.endswith('-label') or qid.endswith('-description'):
                continue
            
            # Skip if already filled or is LinkedIn
            val = q.get_attribute('value') or ''
            if val and val not in ('', 'Select...'):
                continue
            
            # Get label
            label_el = self.frame.query_selector(f'label[for="{qid}"]')
            label = label_el.inner_text().strip() if label_el else qid
            
            # Skip LinkedIn (already handled)
            if 'linkedin' in label.lower():
                continue
            
            # Find answer from defaults
            answer = None
            label_lower = label.lower()
            
            # Find answer from defaults (order matters - more specific first)
            answer = None
            label_lower = label.lower()
            
            for pattern, default_answer in defaults:
                if pattern in label_lower:
                    answer = default_answer
                    break
            
            # Special cases not covered by patterns
            if not answer:
                if 'current company' in label_lower:
                    answer = 'DXC Technology'  # Current employer
                elif 'home address' in label_lower or 'address' in label_lower and 'zip' in label_lower:
                    answer = '1234 Main St, Wake Forest, NC 27587'
                elif 'website' in label_lower or 'portfolio' in label_lower:
                    answer = ''  # Skip optional website
            
            # AI FALLBACK - if no pattern matched and AI is available
            if not answer and self.ai and self.ai.available:
                # Get options if it's a dropdown
                options = []
                role = q.get_attribute('role')
                if role == 'combobox' or q.get_attribute('aria-haspopup'):
                    controls_id = q.get_attribute('aria-controls')
                    if controls_id:
                        # Click to open and get options
                        self.page.keyboard.press('Escape')
                        time.sleep(0.1)
                        q.click()
                        time.sleep(0.3)
                        listbox = self.frame.query_selector(f'#{controls_id}')
                        if listbox:
                            opt_els = listbox.query_selector_all('[role="option"]')
                            options = [o.inner_text().strip() for o in opt_els[:20]]
                        self.page.keyboard.press('Escape')
                        time.sleep(0.1)
                
                # Ask AI
                profile_context = f"Anton Kondakov, Senior TPM/PM, authorized to work in US, no sponsorship needed"
                ai_answer = self.ai.get_answer(label, options=options if options else None, profile_context=profile_context)
                
                if ai_answer:
                    answer = ai_answer
                    print(f"   🤖 AI: '{label[:25]}' → '{answer[:25]}'")
            
            if answer:
                # Check if it's a dropdown (has aria-controls)
                role = q.get_attribute('role')
                if role == 'combobox' or q.get_attribute('aria-haspopup'):
                    self.fill_dropdown(f'#{qid}', answer, label[:30])
                else:
                    # Text field
                    self.fill_text(f'#{qid}', answer, label[:30])
    
    def _final_check(self):
        """Scan ALL fields and report status"""
        print("\n" + "=" * 60)
        print("FINAL FORM SCAN")
        print("=" * 60)
        
        # Scan ALL input fields
        inputs = self.frame.query_selector_all('input:not([type="hidden"]):not([type="submit"]), select, textarea, [role="combobox"]')
        
        filled = []
        empty_required = []
        empty_optional = []
        
        for el in inputs:
            eid = el.get_attribute('id') or ''
            etype = el.get_attribute('type') or ''
            
            # Skip utility fields
            if eid.startswith('iti-') or eid.startswith('g-recaptcha') or etype == 'file':
                continue
            
            # Get label
            label = ""
            if eid:
                label_el = self.frame.query_selector(f'label[for="{eid}"]')
                if label_el:
                    label = label_el.inner_text().strip()[:35]
            
            # Check if required
            required = el.get_attribute('aria-required') == 'true'
            
            # Get value
            val = el.get_attribute('value') or el.input_value() if hasattr(el, 'input_value') else ''
            if not val:
                try:
                    val = el.input_value()
                except:
                    pass
            
            # For dropdowns, check single-value
            if not val:
                sv = el.evaluate('''e => {
                    let sv = e.closest('.select__control')?.parentElement?.querySelector('.select__single-value');
                    return sv ? sv.innerText : '';
                }''')
                val = sv or ''
            
            if val and val not in ('Select...', ''):
                filled.append((eid, label, val[:20]))
            elif required:
                empty_required.append((eid, label))
            else:
                empty_optional.append((eid, label))
        
        # Report
        print(f"\n✅ Filled: {len(filled)}")
        
        if empty_required:
            print(f"\n🔴 EMPTY REQUIRED ({len(empty_required)}):")
            for eid, label in empty_required:
                print(f"    ⚠️ {eid[:25]:<25} | {label}")
        
        if empty_optional:
            print(f"\n⬜ Empty optional ({len(empty_optional)}):")
            for eid, label in empty_optional[:5]:
                print(f"    - {eid[:25]:<25} | {label}")
            if len(empty_optional) > 5:
                print(f"    ... and {len(empty_optional) - 5} more")
        
        # Files
        file_names = self.frame.query_selector_all('.file-upload__filename, [class*="filename"]')
        print(f"\n📎 Files: {len(file_names)}")
        for fn in file_names:
            print(f"    - {fn.inner_text()[:40]}")
        
        # Summary
        print("\n" + "=" * 60)
        if not empty_required:
            print("🎉 ALL REQUIRED FIELDS FILLED!")
        else:
            print(f"⚠️ {len(empty_required)} required fields need attention")


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    import sys
    
    url = sys.argv[1] if len(sys.argv) > 1 else None
    
    filler = FormFillerV6()
    filler.connect()
    
    if not filler.find_frame():
        print("❌ No form found")
        return
    
    # Detect ATS type
    if 'greenhouse' in filler.frame.url:
        filler.fill_greenhouse()
    else:
        print("⚠️ Unknown ATS - using Greenhouse method")
        filler.fill_greenhouse()


if __name__ == "__main__":
    main()
