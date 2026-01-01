#!/usr/bin/env python3
"""
Auto-fill form - non-interactive version for Claude to run.
Returns JSON result instead of waiting for user input.
"""

import sys
import time
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.client import BrowserClient
from browser.smart_filler import SmartFormFiller


def load_profile() -> dict:
    profile_path = Path(__file__).parent / "profiles" / "anton_tpm.json"
    if profile_path.exists():
        with open(profile_path, "r") as f:
            return json.load(f)
    return {}


def auto_fill(url: str, screenshot_name: str = "auto_fill_result.png") -> dict:
    """
    Auto-fill a job application form.
    
    Returns dict with results (no user interaction).
    """
    result = {
        "success": False,
        "url": url,
        "fields_total": 0,
        "fields_filled": 0,
        "fields_skipped": 0,
        "filled_details": [],
        "needs_input": [],
        "screenshot": None,
        "error": None
    }
    
    try:
        profile = load_profile()
        if not profile:
            result["error"] = "Profile not found"
            return result
        
        # Use headless=True for non-interactive
        with BrowserClient(headless=True) as browser:
            if not browser.open_job_page(url, wait_for_cloudflare=False):
                result["error"] = "Failed to open page"
                return result
            
            time.sleep(2)  # Wait for form to load
            
            filler = SmartFormFiller(browser.page, profile, role="TPM")
            filler.scan_form()
            
            # Collect filled field details
            filled_details = []
            
            for field in filler.detected_fields:
                if field.field_type.value == "unknown":
                    continue
                
                answer = filler.db.get_answer(field.field_key, field.field_type, "TPM")
                if answer:
                    value = answer.value
                    if value.startswith("{profile:"):
                        value = filler._resolve_profile_value(value)
                    if value:
                        filled_details.append({
                            "field": field.field_key,
                            "value": value[:50]
                        })
            
            # Fill the form
            filled_count = filler.fill_known_fields()
            
            # Take screenshot
            screenshot_path = browser.screenshot(screenshot_name)
            
            # Build result
            result["success"] = True
            result["fields_total"] = len(filler.detected_fields)
            result["fields_filled"] = filled_count
            result["fields_skipped"] = len(filler.skipped_fields)
            result["filled_details"] = filled_details[:20]  # First 20
            result["needs_input"] = [
                {"label": f.label, "selector": f.selector}
                for f in filler.needs_user_input[:10]
            ]
            result["screenshot"] = str(screenshot_path)
            
    except Exception as e:
        result["error"] = str(e)
    
    return result


if __name__ == "__main__":
    # Default URL or from command line
    url = sys.argv[1] if len(sys.argv) > 1 else "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    result = auto_fill(url)
    print(json.dumps(result, indent=2))
