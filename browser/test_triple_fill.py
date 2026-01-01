#!/usr/bin/env python3
"""
Test Triple Verification + Smart Fill on Coinbase form.
"""

import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright


# Load profile
PROFILE_PATH = PROJECT_ROOT / "browser" / "profiles" / "anton_tpm.json"
with open(PROFILE_PATH) as f:
    PROFILE = json.load(f)


def get_profile_value(key: str) -> str:
    """Get value from profile by dot-notation key."""
    parts = key.split(".")
    value = PROFILE
    
    for part in parts:
        if value is None:
            return ""
        if part.isdigit():
            idx = int(part)
            if isinstance(value, list) and idx < len(value):
                value = value[idx]
            else:
                return ""
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return ""
    
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value) if value else ""


# Field mappings: label pattern -> profile key or direct value
FIELD_MAPPINGS = {
    # Personal
    "first name": "personal.first_name",
    "last name": "personal.last_name", 
    "email": "personal.email",
    "phone": "personal.phone",
    "location": "personal.location",
    "city": "personal.city",
    "country": "personal.country",
    
    # Links
    "linkedin": "links.linkedin",
    "github": "links.github",
    "portfolio": "links.portfolio",
    "website": "links.website",
    
    # Work experience (first entry)
    "company name": "work_experience.0.company",
    "company-name-0": "work_experience.0.company",
    "title-0": "work_experience.0.title",
    "start date month": "work_experience.0.start_month",
    "start-date-month-0": "work_experience.0.start_month",
    "start date year": "work_experience.0.start_year",
    "start-date-year-0": "work_experience.0.start_year",
    
    # Education (first entry)
    "school": "education.0.school",
    "school-name-0": "education.0.school",
    "degree": "education.0.degree",
    "degree-0": "education.0.degree",
    "discipline": "education.0.discipline",
    "discipline-0": "education.0.discipline",
    
    # Common questions - direct values
    "18 years": "Yes",
    "legally authorized": "Yes",
    "authorized to work": "Yes",
    "require sponsorship": "No",
    "sponsorship": "No",
    "how did you hear": "Company website",
    
    # Demographics - decline
    "gender": "Decline to self-identify",
    "veteran": "I am not a protected veteran",
    "disability": "I do not want to answer",
    "race": "Decline to self-identify",
    "hispanic": "Decline to self-identify",
}


def find_answer(label: str, selector: str = "") -> str:
    """Find answer for a field based on label or selector."""
    label_lower = label.lower() if label else ""
    selector_lower = selector.lower() if selector else ""
    
    # Check mappings
    for pattern, value in FIELD_MAPPINGS.items():
        if pattern in label_lower or pattern in selector_lower:
            # If value starts with profile path, get from profile
            if "." in value and not value.startswith(("Yes", "No", "I ", "Decline", "Company")):
                result = get_profile_value(value)
                if result:
                    return result
            else:
                return value
    
    return ""


def fill_field(page, selector: str, value: str, field_type: str = "text") -> bool:
    """Fill a single field."""
    try:
        el = page.query_selector(selector)
        if not el or not el.is_visible():
            return False
        
        if field_type == "select":
            # Try to select by label
            try:
                el.select_option(label=value)
                return True
            except:
                # Try partial match
                options = el.evaluate("el => Array.from(el.options).map(o => ({value: o.value, text: o.text}))")
                for opt in options:
                    if value.lower() in opt["text"].lower():
                        el.select_option(value=opt["value"])
                        return True
        
        elif field_type == "checkbox":
            if value.lower() in ("yes", "true", "1"):
                if not el.is_checked():
                    el.check()
            return True
        
        else:
            # Text input
            el.fill(value)
            return True
            
    except Exception as e:
        print(f"      Error filling {selector}: {e}")
        return False
    
    return False


def test_fill_coinbase():
    """Test filling Coinbase form with triple verification."""
    
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*70)
    print("🧪 TRIPLE VERIFICATION + SMART FILL TEST")
    print("="*70)
    print(f"URL: {url}")
    print(f"Profile: {PROFILE.get('personal', {}).get('first_name', 'Unknown')}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 2000})
        
        print("\n📄 Loading page...")
        page.goto(url, wait_until="networkidle")
        time.sleep(2)
        
        # Screenshot before
        before_path = PROJECT_ROOT / "screenshots" / "coinbase_before_fill.png"
        page.screenshot(path=str(before_path), full_page=True)
        print(f"   📸 Before: {before_path}")
        
        # Parse HTML fields
        print("\n🔍 Analyzing form...")
        fields_data = []
        
        elements = page.query_selector_all("input, select, textarea")
        for el in elements:
            try:
                el_id = el.get_attribute("id") or ""
                el_name = el.get_attribute("name") or ""
                el_type = el.get_attribute("type") or "text"
                tag = el.evaluate("el => el.tagName.toLowerCase()")
                placeholder = el.get_attribute("placeholder") or ""
                aria_label = el.get_attribute("aria-label") or ""
                
                if el_type in ("hidden", "submit", "button"):
                    continue
                
                # Get label
                label = ""
                if el_id:
                    label_el = page.query_selector(f"label[for='{el_id}']")
                    if label_el:
                        label = label_el.inner_text().strip()
                
                # Build selector
                if el_id:
                    selector = f"#{el_id}"
                elif el_name:
                    selector = f"[name='{el_name}']"
                else:
                    continue
                
                # Check if visible
                is_visible = el.is_visible()
                
                if is_visible:
                    fields_data.append({
                        "selector": selector,
                        "id": el_id,
                        "name": el_name,
                        "label": label or aria_label or placeholder or el_id,
                        "type": tag,
                        "input_type": el_type,
                    })
                    
            except:
                continue
        
        print(f"   Found {len(fields_data)} visible fields")
        
        # Fill fields
        print("\n📝 Filling fields...")
        filled = 0
        skipped = 0
        unknown = []
        
        for field in fields_data:
            label = field["label"]
            selector = field["selector"]
            field_type = "select" if field["type"] == "select" else "checkbox" if field["input_type"] == "checkbox" else "text"
            
            # Find answer
            answer = find_answer(label, selector)
            
            if answer:
                success = fill_field(page, selector, answer, field_type)
                if success:
                    filled += 1
                    print(f"   ✅ {label[:35]:<35} = {answer[:25]}")
                else:
                    skipped += 1
                    print(f"   ⚠️  {label[:35]:<35} - fill failed")
            else:
                # Check if it's a required field we don't know
                if "*" in label or field["input_type"] == "file":
                    unknown.append(label)
                skipped += 1
        
        # Handle checkboxes for "current role"
        current_checkbox = page.query_selector("#currently-working-here-0")
        if current_checkbox and current_checkbox.is_visible():
            work_exp = PROFILE.get("work_experience", [])
            if work_exp and work_exp[0].get("current", False):
                try:
                    current_checkbox.check()
                    filled += 1
                    print(f"   ✅ {'Current role checkbox':<35} = checked")
                except:
                    pass
        
        print(f"\n📊 Results: {filled} filled, {skipped} skipped")
        
        if unknown:
            print(f"\n⚠️  Unknown required fields ({len(unknown)}):")
            for u in unknown[:10]:
                print(f"   - {u}")
        
        # Wait for any async updates
        time.sleep(1)
        
        # Screenshot after
        after_path = PROJECT_ROOT / "screenshots" / "coinbase_after_fill.png"
        page.screenshot(path=str(after_path), full_page=True)
        print(f"\n📸 After: {after_path}")
        
        browser.close()
    
    print("\n✅ Test complete!")
    return filled, skipped


if __name__ == "__main__":
    test_fill_coinbase()
