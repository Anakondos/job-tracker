#!/usr/bin/env python3
"""
Test Vision Agent - Disability Status Field (Last field before Submit)

Тестируем последнее поле формы - Disability Status.
Детальные логи ответов LLaVA.
"""

import sys
import json
import time
import base64
import requests
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.client import BrowserClient
from browser.profile import get_profile_manager


# LLaVA config
OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "llava:7b"


def check_ollama():
    """Check if Ollama is running and has LLaVA."""
    print("🔍 Checking Ollama...")
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if r.ok:
            models = [m["name"] for m in r.json().get("models", [])]
            print(f"   Available models: {models}")
            
            if any("llava" in m for m in models):
                print("   ✅ LLaVA is available")
                return True
            else:
                print("   ❌ LLaVA not found. Run: ollama pull llava:7b")
                return False
    except Exception as e:
        print(f"   ❌ Cannot connect to Ollama: {e}")
        print("   Run: ollama serve")
        return False


def encode_image(image_path: str) -> str:
    """Encode image to base64."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def ask_llava(prompt: str, image_path: str) -> dict:
    """
    Send prompt + image to LLaVA.
    Returns detailed response with logs.
    """
    print(f"\n{'='*70}")
    print("📤 LLAVA REQUEST")
    print(f"{'='*70}")
    print(f"Image: {image_path}")
    print(f"Prompt:\n{prompt}")
    print(f"{'='*70}")
    
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [encode_image(image_path)],
        "stream": False,
        "options": {"temperature": 0.1}
    }
    
    try:
        start = time.time()
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
        elapsed = time.time() - start
        
        if resp.ok:
            data = resp.json()
            response_text = data.get("response", "").strip()
            
            print(f"\n{'='*70}")
            print(f"📥 LLAVA RESPONSE ({elapsed:.1f}s)")
            print(f"{'='*70}")
            print(response_text)
            print(f"{'='*70}\n")
            
            return {
                "success": True,
                "response": response_text,
                "elapsed": elapsed,
            }
        else:
            print(f"❌ Error: {resp.status_code} - {resp.text}")
            return {"success": False, "error": resp.text}
            
    except Exception as e:
        print(f"❌ Exception: {e}")
        return {"success": False, "error": str(e)}


def main():
    """Test Disability Status field - last field in form."""
    
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*70)
    print("🧪 TESTING: DISABILITY STATUS (Last Field) - FIXED")
    print("="*70)
    print(f"URL: {url}")
    print(f"Model: {VISION_MODEL}")
    print("Using correct option text: 'I do not want to answer'")
    print("="*70 + "\n")
    
    # Check Ollama
    if not check_ollama():
        return
    
    # Start browser
    print("\n🌐 Starting browser...")
    with BrowserClient(headless=False) as browser:
        
        # Open page
        print(f"📄 Opening: {url}")
        if not browser.open_job_page(url):
            print("❌ Failed to open page")
            return
        
        # Wait for form
        time.sleep(3)
        
        # Scroll to VERY bottom
        print("\n📜 Scrolling to very bottom of form...")
        browser.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        
        # Screenshot BEFORE
        screenshot_before = browser.screenshot("disability_v2_01_before.png")
        print(f"📸 Before screenshot: {screenshot_before}")
        
        # =====================================================
        # STEP 1: Analyze Disability Status field
        # =====================================================
        print("\n" + "#"*70)
        print("# STEP 1: ANALYZE DISABILITY STATUS FIELD")
        print("#"*70)
        
        analysis = ask_llava(
            """Look at this job application form screenshot.

Find the "Disability Status" dropdown field near the bottom.

Tell me:
1. Is the "Disability Status" field visible?
2. Is it currently empty (showing "Select..." or similar)?
3. Do you see the Submit button below it?""",
            str(screenshot_before)
        )
        
        # =====================================================
        # STEP 2: Click and fill
        # =====================================================
        print("\n" + "#"*70)
        print("# STEP 2: FILLING DISABILITY STATUS")
        print("#"*70)
        
        # The correct option text based on actual screenshot
        filter_text = "do not want"  # Shorter, safer filter text
        
        print(f"\n🖱️ Clicking on #disability_status...")
        try:
            el = browser.page.query_selector("#disability_status")
            if el:
                el.scroll_into_view_if_needed()
                time.sleep(0.3)
                el.click()
                print("   ✅ Clicked #disability_status")
                time.sleep(0.5)
                
                # Screenshot with dropdown open
                screenshot_open = browser.screenshot("disability_v2_02_dropdown_open.png")
                print(f"📸 Dropdown open: {screenshot_open}")
                
                # Ask LLaVA what options it sees
                print("\n🔍 Asking LLaVA what options are in dropdown...")
                options_result = ask_llava(
                    """Look at this dropdown menu.

List the EXACT text of each option visible:
1. First option: ...
2. Second option: ...
3. Third option: ...

Which option number is for declining/not wanting to answer?""",
                    str(screenshot_open)
                )
                
                # Type to filter - using "do not want" which should match "I do not want to answer"
                print(f"\n⌨️ Typing '{filter_text}' to filter options...")
                browser.page.keyboard.type(filter_text, delay=50)
                time.sleep(0.5)
                
                # Screenshot after typing
                screenshot_filtered = browser.screenshot("disability_v2_03_filtered.png")
                print(f"📸 Filtered: {screenshot_filtered}")
                
                # Ask LLaVA what's visible after filtering
                print("\n🔍 Asking LLaVA what options remain after filtering...")
                filtered_result = ask_llava(
                    """Look at this dropdown after typing filter text.

How many options are now visible?
What is the text of the visible option(s)?""",
                    str(screenshot_filtered)
                )
                
                # Select with Arrow + Enter
                print("\n⏎ Pressing ArrowDown + Enter to select...")
                browser.page.keyboard.press("ArrowDown")
                time.sleep(0.2)
                browser.page.keyboard.press("Enter")
                time.sleep(0.5)
                
                # Screenshot AFTER
                screenshot_after = browser.screenshot("disability_v2_04_after.png")
                print(f"📸 After: {screenshot_after}")
                
                # =====================================================
                # STEP 3: Verify with LLaVA
                # =====================================================
                print("\n" + "#"*70)
                print("# STEP 3: VERIFY WITH LLAVA")
                print("#"*70)
                
                verify_result = ask_llava(
                    """Look at this form screenshot.

Check the "Disability Status" field:
1. Is there a value selected now? (yes/no)
2. What text is shown in the field?
3. Does it say something like "I do not want to answer"?""",
                    str(screenshot_after)
                )
                
            else:
                print("   ❌ Could not find #disability_status element")
                        
        except Exception as e:
            print(f"   ❌ Error: {e}")
        
        # =====================================================
        # FINAL
        # =====================================================
        print("\n" + "="*70)
        print("⏸️  TEST COMPLETE")
        print("="*70)
        print("\nCheck the browser - was Disability Status filled correctly?")
        print("\nPress Enter to close browser...")
        
        try:
            input()
        except KeyboardInterrupt:
            pass
        
        print("\n✅ Done!")


if __name__ == "__main__":
    main()
