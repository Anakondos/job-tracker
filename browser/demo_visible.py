#!/usr/bin/env python3
"""
VISIBLE browser demo - you can watch it fill the form!
Run this and watch your screen.
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


def demo_visible_fill():
    """Fill form with VISIBLE browser - watch your screen!"""
    
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*60)
    print("👀 VISIBLE BROWSER DEMO")
    print("="*60)
    print("Watch your screen! Browser will open in 3 seconds...")
    print("="*60)
    time.sleep(3)
    
    with sync_playwright() as p:
        # VISIBLE browser!
        browser = p.chromium.launch(
            headless=False,  # ← ТЫ УВИДИШЬ БРАУЗЕР!
            slow_mo=500      # ← Замедление чтобы видеть каждое действие
        )
        
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        
        print("\n📄 Opening page...")
        page.goto(url, wait_until="networkidle")
        time.sleep(2)
        
        print("\n📝 Filling fields (watch the browser!)...\n")
        
        # Fill fields one by one with pauses
        fields = [
            ("#first_name", PROFILE["personal"]["first_name"], "First Name"),
            ("#last_name", PROFILE["personal"]["last_name"], "Last Name"),
            ("#email", PROFILE["personal"]["email"], "Email"),
            ("#phone", PROFILE["personal"]["phone"], "Phone"),
            ("#candidate-location", PROFILE["personal"]["location"], "Location"),
        ]
        
        for selector, value, name in fields:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    el.fill(value)
                    print(f"   ✅ {name}: {value}")
                    time.sleep(0.5)  # Pause to see
            except Exception as e:
                print(f"   ❌ {name}: {e}")
        
        # Fill a dropdown (Greenhouse style)
        print("\n   Filling Country dropdown...")
        try:
            country = page.query_selector("#country")
            if country:
                country.click()
                time.sleep(0.5)
                country.type("United States", delay=100)
                time.sleep(0.5)
                page.keyboard.press("ArrowDown")
                time.sleep(0.3)
                page.keyboard.press("Enter")
                print("   ✅ Country: United States")
        except Exception as e:
            print(f"   ❌ Country: {e}")
        
        print("\n" + "="*60)
        print("✅ Demo complete!")
        print("   Browser will close in 5 seconds...")
        print("   Or close it manually.")
        print("="*60)
        
        time.sleep(5)
        browser.close()


if __name__ == "__main__":
    demo_visible_fill()
