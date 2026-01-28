# ─────────────────────────────────────────────────────────────────────
# PATTERN DEFAULTS
# ─────────────────────────────────────────────────────────────────────

# Order matters! More specific patterns first
PATTERN_DEFAULTS = [
    # Government/Compliance
    ("current government official", "No"),
    ("government official", "No"),
    ("relative of a government", "No"),
    ("conflict of interest", "No"),
    ("referred by", "No"),
    
    # Employment history
    ("previously employed", "No"),
    ("employed by", "No"),
    
    # Work authorization
    ("require sponsorship", "No"),
    ("sponsorship", "No"),
    ("visa", "No"),
    ("legally authorized", "Yes"),
    ("authorized to work", "Yes"),
    
    # Age/Basic
    ("at least 18", "Yes"),
    ("18 years", "Yes"),
    
    # Source
    ("hear about", "LinkedIn"),
    ("how did you", "LinkedIn"),
    
    # Privacy/Consent
    ("privacy", "Confirmed"),
    ("confirm receipt", "Confirmed"),
    ("i understand", "Yes"),
    ("ai tool", "Yes"),
]

# ─────────────────────────────────────────────────────────────────────
# V7 SMART FILLER
# ─────────────────────────────────────────────────────────────────────

class SmartFormFiller:
    """Universal AI-assisted form filler"""
    
    def __init__(self):
        self.page: Optional[Page] = None
        self.frame: Optional[Frame] = None
        self.profile = Profile(PROFILE_PATH)
        self.learned_db = LearnedDB(LEARNED_DB_PATH)
        self.ai_client = None  # Anthropic client
        self.fields: List[FormField] = []
    
    def connect(self, port: int = 9222):
        """Connect to Chrome"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0]
        
        print(f"✅ Connected to Chrome")
        print(f"   URL: {self.page.url[:60]}")
    
    def find_frame(self) -> bool:
        """Find form frame (iframe or main)"""
        for f in self.page.frames:
            if any(ats in f.url for ats in ['greenhouse', 'lever', 'ashby', 'workday']):
                self.frame = f
                print(f"✅ Found ATS iframe")
                return True
        self.frame = self.page.main_frame
        return True
    
    # ─────────────────────────────────────────────────────────────────
    # UNIVERSAL FIELD SCANNER
    # ─────────────────────────────────────────────────────────────────
    
    def scan_fields(self) -> List[FormField]:
        """Scan form and detect ALL fields universally"""
        print("\n🔍 Scanning form fields...")
        
        fields = []
        
        # Find all interactive elements
        selectors = [
            'input:not([type="hidden"]):not([type="submit"])',
            'select',
            'textarea',
            '[role="combobox"]',
            '[role="listbox"]',
        ]
        
        for sel in selectors:
            elements = self.frame.query_selector_all(sel)
            for el in elements:
                field = self._analyze_field(el)
                if field and field.label:
                    fields.append(field)
        
        # Remove duplicates by selector
        seen = set()
        unique_fields = []
        for f in fields:
            if f.selector not in seen:
                seen.add(f.selector)
                unique_fields.append(f)
        
        # Sort by visual position (top to bottom)
        unique_fields.sort(key=lambda f: f.position_y)
        
        self.fields = unique_fields
        print(f"   Found {len(unique_fields)} fields")
        return unique_fields
    
    def _analyze_field(self, el: ElementHandle) -> Optional[FormField]:
        """Analyze single field element"""
        try:
            # Get selector
            field_id = el.get_attribute('id')
            field_name = el.get_attribute('name')
            selector = f"#{field_id}" if field_id else f"[name='{field_name}']" if field_name else None
            
            if not selector:
                return None
            
            # Get label
            label = self._find_label(el, field_id)
            if not label:
                return None
            
            # Detect field type
            field_type = self._detect_field_type(el)
            
            # Get position
            box = el.bounding_box()
            position_y = box['y'] if box else 0
            
            # Get options for dropdowns
            options = []
            if field_type in [FieldType.DROPDOWN, FieldType.SEARCHABLE_DROPDOWN]:
                options = self._get_dropdown_options(el)
            
            # Check if required
            required = el.get_attribute('aria-required') == 'true' or el.get_attribute('required') is not None
            
            return FormField(
                selector=selector,
                label=label,
                field_type=field_type,
                element=el,
                options=options,
                position_y=position_y,
                required=required,
            )
        except:
            return None
    
    def _find_label(self, el: ElementHandle, field_id: str) -> str:
        """Find label for field using multiple methods"""
        # 1. <label for="id">
        if field_id:
            label_el = self.frame.query_selector(f'label[for="{field_id}"]')
            if label_el:
                return label_el.inner_text().strip()
        
        # 2. aria-label
        aria_label = el.get_attribute('aria-label')
        if aria_label:
            return aria_label
        
        # 3. aria-labelledby
        labelledby = el.get_attribute('aria-labelledby')
        if labelledby:
            label_el = self.frame.query_selector(f'#{labelledby}')
            if label_el:
                return label_el.inner_text().strip()
        
        # 4. placeholder
        placeholder = el.get_attribute('placeholder')
        if placeholder:
            return placeholder
        
        # 5. Parent label
        parent_label = el.evaluate('''e => {
            let parent = e.closest('label');
            return parent ? parent.innerText.trim() : '';
        }''')
        if parent_label:
            return parent_label
        
        return ""
    
    def _detect_field_type(self, el: ElementHandle) -> FieldType:
        """Detect field type from element"""
        tag = el.evaluate('e => e.tagName.toLowerCase()')
        input_type = el.get_attribute('type') or ''
        role = el.get_attribute('role') or ''
        aria_haspopup = el.get_attribute('aria-haspopup') or ''
        
        # By tag
        if tag == 'select':
            return FieldType.DROPDOWN
        if tag == 'textarea':
            return FieldType.TEXTAREA
        
        # By input type
        if input_type == 'file':
            return FieldType.FILE
        if input_type == 'checkbox':
            return FieldType.CHECKBOX
        if input_type == 'email':
            return FieldType.EMAIL
        if input_type == 'tel':
            return FieldType.PHONE
        
        # By ARIA
        if role == 'combobox' or aria_haspopup in ('true', 'listbox'):
            # Check if searchable (has aria-autocomplete)
            autocomplete = el.get_attribute('aria-autocomplete')
            if autocomplete == 'list':
                return FieldType.SEARCHABLE_DROPDOWN
            return FieldType.DROPDOWN
        
        return FieldType.TEXT
    
    def _get_dropdown_options(self, el: ElementHandle) -> List[str]:
        """Get dropdown options"""
        options = []
        
        # Try aria-controls
        controls_id = el.get_attribute('aria-controls')
        if controls_id:
            listbox = self.frame.query_selector(f'#{controls_id}')
            if listbox:
                opt_els = listbox.query_selector_all('[role="option"]')
                for opt in opt_els:
                    options.append(opt.inner_text().strip())
        
        return options[:50]  # Limit
