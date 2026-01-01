"""
Triple Verification Form Analyzer

Three-level verification:
1. HTML Parsing - fast, accurate for known patterns
2. Vision AI (Qwen2.5-VL) - visual confirmation  
3. Selector Validation - verify elements exist and are fillable

This ensures high confidence before filling forms.
"""

import json
import time
import base64
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class FieldInfo:
    """Complete field information from all verification levels."""
    
    # From HTML parsing
    selector: str = ""
    element_id: str = ""
    name: str = ""
    html_label: str = ""
    html_type: str = ""       # input, select, textarea
    input_type: str = ""      # text, email, tel, etc
    placeholder: str = ""
    required: bool = False
    options: List[str] = field(default_factory=list)
    
    # From Vision AI
    vision_label: str = ""
    vision_type: str = ""     # What AI thinks this field is
    vision_visible: bool = True
    vision_confidence: float = 0.0
    
    # From Selector Validation
    selector_valid: bool = False
    selector_visible: bool = False
    selector_enabled: bool = False
    current_value: str = ""
    
    # Combined result
    final_label: str = ""
    final_type: str = ""
    confidence: float = 0.0
    verification_notes: List[str] = field(default_factory=list)


class VisionAnalyzer:
    """Qwen2.5-VL based vision analysis."""
    
    def __init__(self, model: str = "qwen2.5vl:3b", url: str = "http://localhost:11434"):
        self.model = model
        self.url = url
    
    def analyze_form(self, screenshot: bytes, html_fields: List[dict]) -> dict:
        """
        Ask Vision AI to verify/supplement HTML findings.
        
        Returns dict with field descriptions from visual analysis.
        """
        image_b64 = base64.b64encode(screenshot).decode("utf-8")
        
        # Build context from HTML findings
        html_context = "\n".join([
            f"- {f.get('label', f.get('id', 'unknown'))}: {f.get('type', 'input')}"
            for f in html_fields[:15]  # Limit to avoid token overflow
        ])
        
        prompt = f"""Analyze this job application form screenshot.

I found these fields from HTML:
{html_context}

For EACH visible form field in the image, tell me:
1. The label text you see
2. Field type (text input, dropdown, checkbox, file upload, etc)
3. Is it empty or filled?
4. Any placeholder text visible?

Also identify any fields you see that are NOT in my HTML list.

Respond in JSON format:
{{
  "fields": [
    {{"label": "...", "type": "...", "empty": true/false, "placeholder": "..."}},
    ...
  ],
  "additional_fields": [
    {{"label": "...", "type": "...", "notes": "..."}}
  ],
  "form_sections": ["Profile", "Education", ...],
  "submit_button_visible": true/false
}}

ONLY output valid JSON."""

        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "images": [image_b64],
                "stream": False,
                "options": {"temperature": 0.1}
            }
            
            resp = requests.post(
                f"{self.url}/api/generate",
                json=payload,
                timeout=120
            )
            
            if resp.ok:
                text = resp.json().get("response", "").strip()
                # Try to parse JSON from response
                import re
                json_match = re.search(r'\{[\s\S]*\}', text)
                if json_match:
                    return json.loads(json_match.group())
            
        except Exception as e:
            print(f"Vision analysis error: {e}")
        
        return {"fields": [], "additional_fields": [], "error": "Analysis failed"}
    
    def verify_single_field(self, screenshot: bytes, field_info: dict) -> dict:
        """Verify a single field visually."""
        image_b64 = base64.b64encode(screenshot).decode("utf-8")
        
        prompt = f"""Look at this form screenshot.

I'm looking for a field with:
- Label: "{field_info.get('label', 'unknown')}"
- Type: {field_info.get('type', 'input')}
- Selector: {field_info.get('selector', 'unknown')}

Answer these questions:
1. Do you see this field? (yes/no)
2. Is it visible and not hidden? (yes/no)
3. What is currently in the field? (empty/has value/placeholder text)
4. Confidence 0-100%?

Respond as JSON:
{{"visible": true/false, "empty": true/false, "current_value": "...", "confidence": 0-100}}"""

        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "images": [image_b64],
                "stream": False,
                "options": {"temperature": 0.1}
            }
            
            resp = requests.post(
                f"{self.url}/api/generate",
                json=payload,
                timeout=60
            )
            
            if resp.ok:
                text = resp.json().get("response", "").strip()
                import re
                json_match = re.search(r'\{[\s\S]*?\}', text)
                if json_match:
                    return json.loads(json_match.group())
                    
        except Exception as e:
            print(f"Field verification error: {e}")
        
        return {"visible": None, "confidence": 0}


class TripleVerifier:
    """
    Three-level form verification system.
    """
    
    def __init__(self, page, use_vision: bool = True):
        self.page = page
        self.use_vision = use_vision
        self.vision = VisionAnalyzer() if use_vision else None
        self.fields: List[FieldInfo] = []
    
    def analyze_form(self) -> List[FieldInfo]:
        """
        Run all three levels of verification.
        """
        print("\n" + "="*60)
        print("🔍 TRIPLE VERIFICATION ANALYSIS")
        print("="*60)
        
        # Level 1: HTML Parsing
        print("\n📄 LEVEL 1: HTML Parsing...")
        html_fields = self._parse_html()
        print(f"   Found {len(html_fields)} fields in DOM")
        
        # Level 2: Vision AI (optional)
        vision_data = {"fields": [], "additional_fields": []}
        if self.use_vision:
            print("\n👁️  LEVEL 2: Vision AI Analysis...")
            screenshot = self.page.screenshot(type="png")
            vision_data = self.vision.analyze_form(
                screenshot,
                [{"label": f.html_label, "id": f.element_id, "type": f.input_type} 
                 for f in html_fields]
            )
            print(f"   Vision found {len(vision_data.get('fields', []))} fields")
            if vision_data.get('additional_fields'):
                print(f"   + {len(vision_data['additional_fields'])} additional fields not in HTML!")
        
        # Level 3: Selector Validation
        print("\n✓  LEVEL 3: Selector Validation...")
        validated = self._validate_selectors(html_fields)
        print(f"   Validated {validated} selectors")
        
        # Merge results
        print("\n🔗 Merging verification levels...")
        self._merge_results(html_fields, vision_data)
        
        # Summary
        self._print_summary()
        
        return self.fields
    
    def _parse_html(self) -> List[FieldInfo]:
        """Level 1: Parse HTML/DOM for form fields."""
        fields = []
        
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
                label = ""
                if el_id:
                    label_el = self.page.query_selector(f"label[for='{el_id}']")
                    if label_el:
                        label = label_el.inner_text().strip()
                
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
                            "el => Array.from(el.options).map(o => o.text)"
                        )
                    except:
                        pass
                
                field = FieldInfo(
                    selector=selector,
                    element_id=el_id,
                    name=el_name,
                    html_label=label or aria_label or placeholder,
                    html_type=tag,
                    input_type=el_type,
                    placeholder=placeholder,
                    required=required,
                    options=options
                )
                
                fields.append(field)
                
            except Exception as e:
                continue
        
        return fields
    
    def _validate_selectors(self, fields: List[FieldInfo]) -> int:
        """Level 3: Validate each selector actually works."""
        validated = 0
        
        for field in fields:
            try:
                el = self.page.query_selector(field.selector)
                
                if el:
                    field.selector_valid = True
                    field.selector_visible = el.is_visible()
                    field.selector_enabled = el.is_enabled()
                    
                    # Get current value
                    try:
                        if field.html_type == "select":
                            field.current_value = el.evaluate(
                                "el => el.options[el.selectedIndex]?.text || ''"
                            )
                        else:
                            field.current_value = el.input_value() or ""
                    except:
                        pass
                    
                    if field.selector_visible and field.selector_enabled:
                        validated += 1
                        field.verification_notes.append("✓ Selector valid and visible")
                    else:
                        field.verification_notes.append("⚠ Selector valid but not visible/enabled")
                else:
                    field.verification_notes.append("✗ Selector not found")
                    
            except Exception as e:
                field.verification_notes.append(f"✗ Selector error: {e}")
        
        return validated
    
    def _merge_results(self, html_fields: List[FieldInfo], vision_data: dict):
        """Merge all verification levels into final result."""
        
        vision_fields = {
            f.get("label", "").lower(): f 
            for f in vision_data.get("fields", [])
        }
        
        for field in html_fields:
            # Try to match with vision data
            html_label_lower = field.html_label.lower() if field.html_label else ""
            
            vision_match = None
            for v_label, v_data in vision_fields.items():
                if v_label and (v_label in html_label_lower or html_label_lower in v_label):
                    vision_match = v_data
                    break
            
            if vision_match:
                field.vision_label = vision_match.get("label", "")
                field.vision_type = vision_match.get("type", "")
                field.vision_visible = not vision_match.get("empty", True)
                field.vision_confidence = vision_match.get("confidence", 50) / 100
                field.verification_notes.append("✓ Vision confirmed")
            elif self.use_vision:
                field.verification_notes.append("⚠ Not found by Vision AI")
            
            # Calculate final confidence
            confidence = 0.0
            
            # HTML found it
            if field.html_label:
                confidence += 0.4
            
            # Selector works
            if field.selector_valid and field.selector_visible:
                confidence += 0.4
            
            # Vision confirmed
            if vision_match:
                confidence += 0.2
            
            field.confidence = min(confidence, 1.0)
            
            # Set final values
            field.final_label = field.html_label or field.vision_label or field.name
            field.final_type = field.input_type or field.vision_type or "text"
        
        self.fields = html_fields
        
        # Add fields found only by vision
        for v_field in vision_data.get("additional_fields", []):
            extra = FieldInfo(
                vision_label=v_field.get("label", ""),
                vision_type=v_field.get("type", ""),
                vision_visible=True,
                confidence=0.3,  # Lower confidence - only vision saw it
                verification_notes=["⚠ Only found by Vision AI, no HTML match"]
            )
            extra.final_label = extra.vision_label
            extra.final_type = extra.vision_type
            self.fields.append(extra)
    
    def _print_summary(self):
        """Print verification summary."""
        print("\n" + "-"*60)
        print("📊 VERIFICATION SUMMARY")
        print("-"*60)
        
        high_conf = [f for f in self.fields if f.confidence >= 0.8]
        medium_conf = [f for f in self.fields if 0.5 <= f.confidence < 0.8]
        low_conf = [f for f in self.fields if f.confidence < 0.5]
        
        print(f"   ✅ High confidence (≥80%):   {len(high_conf)} fields")
        print(f"   ⚠️  Medium confidence:        {len(medium_conf)} fields")
        print(f"   ❌ Low confidence (<50%):    {len(low_conf)} fields")
        
        print("\n   Top fields:")
        for f in sorted(self.fields, key=lambda x: x.confidence, reverse=True)[:10]:
            conf_icon = "✅" if f.confidence >= 0.8 else "⚠️" if f.confidence >= 0.5 else "❌"
            print(f"   {conf_icon} {f.final_label[:30]:<30} | {f.confidence*100:.0f}% | {f.selector[:20]}")
        
        print("-"*60)
    
    def get_high_confidence_fields(self) -> List[FieldInfo]:
        """Get only fields with high confidence."""
        return [f for f in self.fields if f.confidence >= 0.8]
    
    def get_fields_needing_review(self) -> List[FieldInfo]:
        """Get fields that need manual review."""
        return [f for f in self.fields if f.confidence < 0.8]


def test_triple_verification():
    """Test the triple verification system."""
    from playwright.sync_api import sync_playwright
    
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*70)
    print("🧪 TRIPLE VERIFICATION TEST")
    print("="*70)
    print(f"URL: {url}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        
        page.goto(url, wait_until="networkidle")
        time.sleep(2)
        
        # Run triple verification
        verifier = TripleVerifier(page, use_vision=True)
        fields = verifier.analyze_form()
        
        # Results
        print("\n" + "="*70)
        print("📋 RESULTS")
        print("="*70)
        
        high_conf = verifier.get_high_confidence_fields()
        print(f"\n✅ Ready to auto-fill ({len(high_conf)} fields):")
        for f in high_conf[:15]:
            print(f"   - {f.final_label[:40]}")
        
        need_review = verifier.get_fields_needing_review()
        if need_review:
            print(f"\n⚠️  Need review ({len(need_review)} fields):")
            for f in need_review[:10]:
                print(f"   - {f.final_label[:40]} ({f.confidence*100:.0f}%)")
                for note in f.verification_notes:
                    print(f"     {note}")
        
        browser.close()
    
    print("\n✅ Test complete!")


if __name__ == "__main__":
    test_triple_verification()
