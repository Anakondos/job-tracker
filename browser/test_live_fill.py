"""
Test form fill with LIVE streaming to browser monitor.

Run in two terminals:
  Terminal 1: python browser/live_monitor.py
  Terminal 2: python browser/test_live_fill.py
  
Then open http://localhost:8765 in browser to watch!
"""

import sys
sys.path.insert(0, '.')

from playwright.sync_api import sync_playwright
from browser.live_monitor import start_monitor_server, send_screenshot, send_status
import json
import time
from pathlib import Path

job_url = "https://job-boards.greenhouse.io/pagerduty/jobs/5691990004"

with open("browser/profiles/anton_tpm.json") as f:
    profile = json.load(f)

print("="*60)
print("🎬 Live Form Fill with Browser Streaming")
print("="*60)

# Start monitor server
start_monitor_server()
print("\n⏳ Waiting 3 sec for you to open http://localhost:8765 ...")
time.sleep(3)

with sync_playwright() as p:
    # Use headless=True since we're streaming to browser
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    
    def stream(msg=""):
        """Send screenshot + status to browser."""
        if msg:
            send_status(msg)
            print(f"   {msg}")
        send_screenshot(page)
        time.sleep(0.1)  # Small delay for smooth streaming
    
    # Navigate
    send_status("📍 Opening job page...")
    page.goto(job_url, wait_until="networkidle", timeout=30000)
    stream("📍 Job page loaded")
    time.sleep(1)
    
    # Click Apply
    stream("🔘 Clicking Apply button...")
    page.locator('button:has-text("Apply")').first.click()
    time.sleep(2)
    stream("✅ On application form")
    
    # Fill form with streaming
    stream("✍️ Filling First Name...")
    page.locator('#first_name').fill(profile['personal']['first_name'])
    stream()
    
    stream("✍️ Filling Last Name...")
    page.locator('#last_name').fill(profile['personal']['last_name'])
    stream()
    
    stream("✍️ Filling Email...")
    page.locator('#email').fill(profile['personal']['email'])
    stream()
    
    stream("✍️ Filling Phone...")
    page.locator('#phone').fill(profile['personal']['phone'])
    stream()
    
    # Country
    stream("🌍 Selecting Country...")
    try:
        page.locator('[class*="country"] .select__control').first.click()
        time.sleep(0.3)
        stream()
        page.keyboard.type("United States")
        time.sleep(0.5)
        stream()
        page.locator('[class*="option"]').first.click()
        stream("✅ Country selected")
    except Exception as e:
        stream(f"⚠️ Country: {e}")
    
    # Upload CV
    stream("📎 Uploading CV...")
    cv_path = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Dev/AI_projects/Gold CV/CV_Anton_Kondakov_Product Manager.pdf"
    try:
        page.locator('input[type="file"]').first.set_input_files(str(cv_path))
        time.sleep(1)
        stream("✅ CV uploaded")
    except Exception as e:
        stream(f"⚠️ CV: {e}")
    
    # Scroll to show more
    page.evaluate("window.scrollTo(0, 500)")
    stream("📜 Scrolled down")
    
    # Final screenshot
    stream("🎉 Form filling complete!")
    time.sleep(3)
    
    browser.close()

print("\n✅ Done! Check the browser monitor.")
