#!/usr/bin/env python3
"""Click Apply on Meta and analyze what happens."""

import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.sync_api import sync_playwright


def click_apply_meta():
    job_url = "https://www.metacareers.com/jobs/1296230207698571"
    
    print("\n" + "="*70)
    print("🖱️  CLICKING APPLY ON META")
    print("="*70)
    
    intercepted_after_click = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        
        # Intercept after click
        def on_request(request):
            if request.resource_type in ("xhr", "fetch"):
                intercepted_after_click.append({
                    "url": request.url,
                    "method": request.method,
                })
        
        page.on("request", on_request)
        
        # Navigate
        print("📄 Loading job page...")
        page.goto(job_url, wait_until="networkidle", timeout=30000)
        time.sleep(2)
        
        # Screenshot before
        page.screenshot(path=str(PROJECT_ROOT / "screenshots" / "meta_before_apply.png"))
        
        # Find Apply button with various selectors
        print("\n🔍 Looking for Apply button...")
        
        selectors = [
            "text=Apply now",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "[data-testid*='apply']",
            "div[role='button']:has-text('Apply')",
        ]
        
        apply_btn = None
        for sel in selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    apply_btn = btn
                    print(f"   ✅ Found with: {sel}")
                    break
            except:
                pass
        
        if not apply_btn:
            print("   ❌ Apply button not found with standard selectors")
            print("   Trying to find any clickable element with 'Apply'...")
            
            # Get all elements with Apply text
            elements = page.query_selector_all("*")
            for el in elements:
                try:
                    text = el.inner_text()
                    if "Apply" in text and len(text) < 20:
                        tag = el.evaluate("el => el.tagName")
                        print(f"      Found: <{tag}> '{text}'")
                        if tag in ["BUTTON", "A", "DIV", "SPAN"]:
                            apply_btn = el
                            break
                except:
                    pass
        
        if apply_btn:
            print("\n🖱️  Clicking Apply...")
            intercepted_after_click.clear()
            
            try:
                apply_btn.click()
                time.sleep(3)
                
                # Check current URL
                new_url = page.url
                print(f"   New URL: {new_url}")
                
                # Screenshot after
                page.screenshot(path=str(PROJECT_ROOT / "screenshots" / "meta_after_apply.png"))
                print(f"   📸 Screenshot saved")
                
                # Check for new requests
                print(f"\n   📡 New requests after click: {len(intercepted_after_click)}")
                for req in intercepted_after_click[:10]:
                    if "graphql" in req["url"].lower() or "apply" in req["url"].lower():
                        print(f"      {req['method']} {req['url'][:80]}")
                
                # Check for form elements now
                forms = page.query_selector_all("form")
                inputs = page.query_selector_all("input:not([type='hidden'])")
                print(f"\n   📝 Forms: {len(forms)}, Inputs: {len(inputs)}")
                
                for inp in inputs[:15]:
                    try:
                        inp_type = inp.get_attribute("type") or "text"
                        inp_name = inp.get_attribute("name") or inp.get_attribute("id") or ""
                        placeholder = inp.get_attribute("placeholder") or ""
                        aria_label = inp.get_attribute("aria-label") or ""
                        label = placeholder or aria_label or inp_name
                        print(f"      [{inp_type:10}] {label[:40]}")
                    except:
                        pass
                
                # Check for login requirement
                if "login" in new_url.lower() or page.query_selector("input[type='password']"):
                    print("\n   ⚠️  LOGIN REQUIRED!")
                
            except Exception as e:
                print(f"   ❌ Click failed: {e}")
        
        browser.close()
    
    print("\n✅ Done!")


if __name__ == "__main__":
    click_apply_meta()
