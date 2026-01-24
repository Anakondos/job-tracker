#!/usr/bin/env python3
"""
Quick Form Fill - Test script for Context Discovery approach

Usage:
    python quick_fill.py <url>
    
This script:
1. Connects to existing Chrome (debug port 9222)
2. Discovers all form fields with Context Discovery
3. Maps fields to profile data
4. Fills the form
5. Logs results
"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from playwright.sync_api import sync_playwright
from browser.v5.context_discovery import ContextDiscovery, fill_field_in_shadow, click_radio_in_shadow, check_form_errors
from browser.v5.form_logger import FormLogger


def load_profile():
    """Load profile data."""
    profile_path = Path(__file__).parent.parent / "profiles" / "anton_tpm.json"
    with open(profile_path) as f:
        return json.load(f)


def match_field_to_profile(question: str, profile: dict) -> tuple:
    """
    Match a field question to profile data.
    Returns (value, source) or (None, None) if no match.
    """
    q = question.lower()
    
    # Personal info
    if 'first name' in q:
        return profile['personal']['first_name'], 'profile'
    if 'last name' in q:
        return profile['personal']['last_name'], 'profile'
    if 'email' in q:
        return profile['personal']['email'], 'profile'
    if 'phone' in q:
        return profile['personal']['phone'], 'profile'
    
    # Address
    if 'street' in q or 'address' in q or 'main st' in q:
        return profile['personal']['street_address'], 'profile'
    if 'city' in q or 'beverly' in q:
        return profile['personal']['city'], 'profile'
    if 'zip' in q or 'postal' in q or '90210' in q:
        return profile['personal']['zip_code'], 'profile'
    if 'state' in q:
        return profile['personal']['state'], 'profile'
    
    # Professional
    if 'job title' in q or 'current.*title' in q or 'recent.*title' in q:
        return profile['work_experience'][0]['title'], 'profile'
    if 'company' in q or 'employer' in q:
        return profile['work_experience'][0]['company'], 'profile'
    
    # Salary - just number
    if 'salary' in q or 'pay' in q or 'compensation' in q:
        return '150000', 'default'
    
    return None, None


def match_radio_to_answer(label: str, profile: dict) -> bool:
    """
    Determine if a radio button should be selected based on its label.
    Returns True if should click.
    """
    l = label.lower()
    
    # Work authorization - Yes
    if 'yes' == l and 'authorized' in str(profile):  # This is a simplification
        return True
    
    # Education - Masters
    if 'masters' == l or 'master' in l:
        return True
    
    # Experience - 10+ years  
    if '10+' in l or '10 years' in l:
        return True
    
    # Contract-to-hire - Yes (for TEKsystems specifically)
    # Work settings - Hybrid, Remote
    if l in ('hybrid', 'remote'):
        return True
    
    # References - Yes
    if 'yes' == l:
        return True
    
    # Text messages - agree
    if 'agree' in l and 'text' in l:
        return True
    
    return False


def main(url: str):
    print("=" * 60)
    print("Quick Form Fill with Context Discovery")
    print("=" * 60)
    print(f"URL: {url[:60]}...")
    
    profile = load_profile()
    logger = FormLogger()
    
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.pages[0]
        
        # Navigate if needed
        if url not in page.url:
            page.goto(url)
            page.wait_for_load_state('networkidle')
        
        logger.start_session(url, page.title())
        
        # Discover fields
        print("\n🔍 Discovering fields...")
        discovery = ContextDiscovery(page)
        fields = discovery.discover_all_fields()
        
        print(f"   Found {len(fields)} fields")
        
        # Fill text fields
        print("\n📝 Filling text fields...")
        filled_count = 0
        
        for field in fields:
            if field['type'] in ('text', 'email', 'tel') and not field['value']:
                question = discovery.get_field_question(field)
                value, source = match_field_to_profile(question, profile)
                
                if value:
                    success = fill_field_in_shadow(page, field['id'], value)
                    status = '✅' if success else '❌'
                    print(f"   {status} {question[:40]:40} = {value[:20]}")
                    
                    logger.log_field(
                        field_id=field['id'],
                        field_type=field['type'],
                        question=question,
                        value=value,
                        source=source,
                        success=success
                    )
                    
                    if success:
                        filled_count += 1
                    
                    time.sleep(0.1)
        
        print(f"\n   Filled {filled_count} text fields")
        
        # Handle radios and checkboxes
        print("\n🔘 Selecting options...")
        
        # Get all radio/checkbox options
        options = page.evaluate('''() => {
            const results = [];
            
            function findInShadow(root) {
                const inputs = root.querySelectorAll('input[type="radio"]:not(:checked), input[type="checkbox"]:not(:checked)');
                for (const input of inputs) {
                    const label = root.querySelector(`label[for="${input.id}"]`);
                    if (label) {
                        results.push({
                            id: input.id,
                            type: input.type,
                            label: label.textContent.trim()
                        });
                    }
                }
                
                const all = root.querySelectorAll('*');
                for (const el of all) {
                    if (el.shadowRoot) findInShadow(el.shadowRoot);
                }
            }
            
            findInShadow(document);
            return results;
        }''')
        
        # Click matching options
        clicked = 0
        for opt in options:
            if match_radio_to_answer(opt['label'], profile):
                # Get partial ID (without suffix)
                id_part = opt['id'].rsplit('-', 1)[0] if '-' in opt['id'] else opt['id']
                success = click_radio_in_shadow(page, id_part)
                if success:
                    print(f"   ✅ {opt['label'][:40]}")
                    clicked += 1
                    time.sleep(0.1)
        
        print(f"\n   Selected {clicked} options")
        
        # Check for errors
        print("\n🔎 Checking for errors...")
        errors = check_form_errors(page)
        
        if errors:
            print(f"   ⚠️ Found {len(errors)} errors:")
            for e in errors[:5]:
                print(f"      - {e[:60]}")
                logger.log_error(e)
        else:
            print("   ✅ No errors found")
        
        # Save log
        log_path = logger.end_session("completed")
        print(f"\n📋 Log saved: {log_path}")
        
        # Take final screenshot
        screenshot_path = Path(log_path).with_suffix('.png')
        page.screenshot(path=str(screenshot_path))
        print(f"📸 Screenshot: {screenshot_path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python quick_fill.py <url>")
        sys.exit(1)
    
    main(sys.argv[1])
