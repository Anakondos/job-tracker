"""
Form Analyzer - Triple verification approach.

Combines:
1. HTML/DOM parsing (selectors, labels, attributes)
2. Vision AI (visual context, user perspective)  
3. Network interception (actual API schema)

This gives us the most reliable form understanding.
"""

import json
import time
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FormField:
    """Complete field information from all sources."""
    
    # From HTML
    selector: str = ""
    element_id: str = ""
    name: str = ""
    html_type: str = ""         # input, select, textarea
    input_type: str = ""        # text, email, tel, etc
    label_text: str = ""
    placeholder: str = ""
    required: bool = False
    options: List[str] = field(default_factory=list)
    
    # From Vision
    visual_label: str = ""
    visual_position: str = ""   # top, middle, bottom
    visual_context: str = ""    # what's around the field
    is_visible: bool = True
    
    # From Network/API
    api_field_name: str = ""
    api_required: bool = False
    api_type: str = ""
    api_validation: str = ""
    
    # Computed
    confidence: float = 0.0
    best_label: str = ""        # Merged from all sources
    

@dataclass
class FormSchema:
    """Complete form schema from all sources."""
    
    form_action: str = ""       # Where form submits
    form_method: str = ""       # POST, PUT, etc
    fields: List[FormField] = field(default_factory=list)
    api_endpoint: str = ""
    api_schema: Dict = field(default_factory=dict)
    

class FormAnalyzer:
    """
    Analyzes forms using triple verification.
    """
    
    def __init__(self, page):
        self.page = page
        self.intercepted_requests = []
        self.intercepted_responses = []
        self._setup_network_interception()
    
    def _setup_network_interception(self):
        """Setup network request/response interception."""
        
        def handle_request(request):
            # Capture form submissions and API calls
            if request.method in ("POST", "PUT", "PATCH"):
                self.intercepted_requests.append({
                    "url": request.url,
                    "method": request.method,
                    "post_data": request.post_data,
                    "headers": dict(request.headers),
                })
        
        def handle_response(response):
            # Capture API responses that might contain schema
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = response.json()
                    self.intercepted_responses.append({
                        "url": response.url,
                        "status": response.status,
                        "body": body,
                    })
                except:
                    pass
        
        self.page.on("request", handle_request)
        self.page.on("response", handle_response)
    
    def analyze_html(self) -> List[FormField]:
        """
        Extract field information from HTML/DOM.
        """
        fields = []
        
        # Find all form elements
        elements = self.page.query_selector_all("input, select, textarea")
        
        for el in elements:
            try:
                el_id = el.get_attribute("id") or ""
                el_name = el.get_attribute("name") or ""
                el_type = el.get_attribute("type") or "text"
                tag = el.evaluate("el => el.tagName.toLowerCase()")
                placeholder = el.get_attribute("placeholder") or ""
                required = el.get_attribute("required") is not None
                aria_label = el.get_attribute("aria-label") or ""
                
                # Skip hidden/system fields
                if el_type in ("hidden", "submit", "button"):
                    continue
                
                # Find label
                label_text = ""
                if el_id:
                    label_el = self.page.query_selector(f"label[for='{el_id}']")
                    if label_el:
                        label_text = label_el.inner_text().strip()
                
                # Build selector
                if el_id:
                    selector = f"#{el_id}"
                elif el_name:
                    selector = f"[name='{el_name}']"
                else:
                    continue
                
                # Get options for select
                options = []
                if tag == "select":
                    try:
                        options = el.evaluate(
                            "el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))"
                        )
                    except:
                        pass
                
                # Check visibility
                is_visible = el.is_visible()
                
                field = FormField(
                    selector=selector,
                    element_id=el_id,
                    name=el_name,
                    html_type=tag,
                    input_type=el_type,
                    label_text=label_text or aria_label or placeholder,
                    placeholder=placeholder,
                    required=required,
                    options=[o.get("text", "") for o in options] if options else [],
                    is_visible=is_visible,
                )
                
                fields.append(field)
                
            except Exception as e:
                continue
        
        return fields
    
    def analyze_form_action(self) -> Dict:
        """
        Extract form submission details.
        """
        forms = self.page.query_selector_all("form")
        
        form_info = {
            "action": "",
            "method": "POST",
            "enctype": "",
        }
        
        for form in forms:
            action = form.get_attribute("action") or ""
            method = form.get_attribute("method") or "POST"
            enctype = form.get_attribute("enctype") or ""
            
            if action:
                form_info = {
                    "action": action,
                    "method": method.upper(),
                    "enctype": enctype,
                }
                break
        
        return form_info
    
    def analyze_scripts_for_schema(self) -> Dict:
        """
        Look for API schema in page scripts (common in React/SPA apps).
        
        Many modern apps embed their API schema or validation rules
        in JavaScript variables or data attributes.
        """
        schema = {}
        
        # Look for common patterns
        patterns = [
            # React apps often have __NEXT_DATA__ or similar
            r'__NEXT_DATA__\s*=\s*(\{.*?\});',
            # Apollo/GraphQL schemas
            r'__APOLLO_STATE__\s*=\s*(\{.*?\});',
            # Generic JSON config
            r'window\.__CONFIG__\s*=\s*(\{.*?\});',
            r'window\.initialData\s*=\s*(\{.*?\});',
        ]
        
        page_content = self.page.content()
        
        for pattern in patterns:
            match = re.search(pattern, page_content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    schema["embedded_data"] = data
                    break
                except:
                    pass
        
        # Look for data attributes that might contain field config
        elements_with_data = self.page.query_selector_all("[data-field-config], [data-validation]")
        if elements_with_data:
            schema["field_configs"] = []
            for el in elements_with_data:
                config = el.get_attribute("data-field-config") or el.get_attribute("data-validation")
                if config:
                    try:
                        schema["field_configs"].append(json.loads(config))
                    except:
                        schema["field_configs"].append(config)
        
        return schema
    
    def trigger_validation_to_find_fields(self) -> List[Dict]:
        """
        Try to submit empty form to trigger validation errors.
        This reveals required fields and their expected formats.
        """
        validation_info = []
        
        # Find submit button
        submit = self.page.query_selector(
            "button[type='submit'], input[type='submit'], button:has-text('Submit'), button:has-text('Apply')"
        )
        
        if submit:
            # Click submit to trigger validation
            try:
                submit.click()
                time.sleep(1)
                
                # Look for validation error messages
                error_elements = self.page.query_selector_all(
                    ".error, .validation-error, [role='alert'], .invalid-feedback, "
                    "[aria-invalid='true'], .field-error"
                )
                
                for err in error_elements:
                    text = err.inner_text().strip()
                    # Try to find associated field
                    parent = err.evaluate("el => el.closest('.form-group, .field-wrapper, .input-group')?.querySelector('input, select')?.name")
                    
                    validation_info.append({
                        "error_text": text,
                        "field_name": parent,
                    })
                    
            except Exception as e:
                print(f"Validation trigger failed: {e}")
        
        return validation_info
    
    def capture_submit_request(self) -> Optional[Dict]:
        """
        Capture the actual form submission request.
        This is the GROUND TRUTH of what the form expects.
        """
        # Clear previous
        self.intercepted_requests = []
        
        # Try to find and click submit
        submit = self.page.query_selector(
            "button[type='submit'], input[type='submit']"
        )
        
        if submit:
            try:
                submit.click()
                time.sleep(2)
                
                # Check intercepted requests
                for req in self.intercepted_requests:
                    if req["method"] in ("POST", "PUT"):
                        return {
                            "url": req["url"],
                            "method": req["method"],
                            "payload": self._parse_post_data(req.get("post_data", "")),
                        }
            except:
                pass
        
        return None
    
    def _parse_post_data(self, data: str) -> Dict:
        """Parse POST data (form-urlencoded or JSON)."""
        if not data:
            return {}
        
        try:
            # Try JSON first
            return json.loads(data)
        except:
            pass
        
        # Try form-urlencoded
        try:
            from urllib.parse import parse_qs
            parsed = parse_qs(data)
            return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        except:
            pass
        
        return {"raw": data}
    
    def full_analysis(self, use_vision: bool = False) -> FormSchema:
        """
        Perform complete form analysis using all sources.
        
        Args:
            use_vision: Whether to also use AI vision analysis
            
        Returns:
            Complete FormSchema with high confidence field mapping
        """
        print("\n🔍 Analyzing form...")
        
        # 1. HTML Analysis
        print("   📄 Parsing HTML...")
        html_fields = self.analyze_html()
        print(f"      Found {len(html_fields)} fields in DOM")
        
        # 2. Form action
        print("   📤 Checking form action...")
        form_info = self.analyze_form_action()
        print(f"      Action: {form_info.get('action', 'N/A')}")
        
        # 3. Script analysis
        print("   📜 Analyzing scripts for schema...")
        script_schema = self.analyze_scripts_for_schema()
        if script_schema:
            print(f"      Found embedded data!")
        
        # 4. Network interception (passive - from previous requests)
        print("   🌐 Checking intercepted network requests...")
        if self.intercepted_responses:
            print(f"      Captured {len(self.intercepted_responses)} API responses")
        
        # Build schema
        schema = FormSchema(
            form_action=form_info.get("action", ""),
            form_method=form_info.get("method", "POST"),
            fields=html_fields,
            api_schema=script_schema,
        )
        
        # Calculate confidence for each field
        for field in schema.fields:
            confidence = 0.5  # Base
            
            if field.label_text:
                confidence += 0.2
            if field.element_id:
                confidence += 0.1
            if field.name:
                confidence += 0.1
            if field.is_visible:
                confidence += 0.1
            
            field.confidence = min(confidence, 1.0)
            field.best_label = field.label_text or field.placeholder or field.name or field.element_id
        
        return schema
    
    def print_schema(self, schema: FormSchema):
        """Pretty print the form schema."""
        print("\n" + "="*70)
        print("📋 FORM SCHEMA")
        print("="*70)
        print(f"Action: {schema.form_action}")
        print(f"Method: {schema.form_method}")
        print(f"Fields: {len(schema.fields)}")
        print("-"*70)
        
        for f in schema.fields:
            req = "* " if f.required else "  "
            vis = "👁" if f.is_visible else "🚫"
            print(f"{req}{vis} {f.best_label[:30]:<30} | {f.selector:<25} | {f.input_type}")
        
        print("="*70)


class SmartFormFiller:
    """
    Enhanced form filler using triple verification.
    """
    
    def __init__(self, page, profile: dict):
        self.page = page
        self.profile = profile
        self.analyzer = FormAnalyzer(page)
    
    def analyze_and_fill(self) -> Dict:
        """
        Analyze form and fill with high confidence.
        """
        # Get schema
        schema = self.analyzer.full_analysis()
        self.analyzer.print_schema(schema)
        
        result = {
            "total_fields": len(schema.fields),
            "filled": 0,
            "skipped": 0,
            "unknown": 0,
            "details": []
        }
        
        # Fill each field
        for field in schema.fields:
            if not field.is_visible:
                result["skipped"] += 1
                continue
            
            # Find answer
            answer = self._find_answer(field)
            
            if answer:
                success = self._fill_field(field, answer)
                if success:
                    result["filled"] += 1
                    result["details"].append({
                        "field": field.best_label,
                        "value": answer[:30],
                        "confidence": field.confidence
                    })
                else:
                    result["skipped"] += 1
            else:
                result["unknown"] += 1
                result["details"].append({
                    "field": field.best_label,
                    "status": "unknown"
                })
        
        return result
    
    def _find_answer(self, field: FormField) -> Optional[str]:
        """Find answer for field from profile."""
        # ... similar to previous implementation
        label = field.best_label.lower()
        
        patterns = {
            "first name": self._get_profile("personal.first_name"),
            "last name": self._get_profile("personal.last_name"),
            "email": self._get_profile("personal.email"),
            "phone": self._get_profile("personal.phone"),
            # ... etc
        }
        
        for pattern, value in patterns.items():
            if pattern in label and value:
                return value
        
        return None
    
    def _get_profile(self, key: str) -> str:
        """Get value from profile."""
        parts = key.split(".")
        value = self.profile
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return ""
        return str(value) if value else ""
    
    def _fill_field(self, field: FormField, value: str) -> bool:
        """Fill a field."""
        try:
            el = self.page.query_selector(field.selector)
            if el and el.is_visible():
                if field.html_type == "select":
                    el.select_option(label=value)
                else:
                    el.fill(value)
                return True
        except:
            pass
        return False
