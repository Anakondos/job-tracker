#!/usr/bin/env python3
"""
Test Smart Form Filler - Database + Profile approach.

Тестируем умное заполнение:
1. Сканирует форму
2. Заполняет известные поля из базы + профиля
3. Для неизвестных - спрашивает пользователя
4. Сохраняет новые ответы в базу
"""

import sys
import time
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from browser.client import BrowserClient
from browser.smart_filler import SmartFormFiller


def load_profile() -> dict:
    """Load the TPM profile."""
    profile_path = Path(__file__).parent / "profiles" / "anton_tpm.json"
    if profile_path.exists():
        with open(profile_path, "r") as f:
            return json.load(f)
    return {}


def main():
    """Test smart form filling."""
    
    url = "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*70)
    print("🧠 SMART FORM FILLER TEST v2")
    print("="*70)
    print(f"URL: {url}")
    print("This will:")
    print("  1. Scan form for all fields")
    print("  2. Fill known fields from database + profile (fast!)")
    print("  3. Ask you about unknown fields")
    print("  4. Save new answers to database for next time")
    print("="*70 + "\n")
    
    # Load profile
    profile = load_profile()
    first_name = profile.get("personal", {}).get("first_name", "(not found)")
    print(f"📋 Profile loaded: {first_name}")
    print(f"   Work experience: {len(profile.get('work_experience', []))} entries")
    print(f"   Education: {len(profile.get('education', []))} entries")
    
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
        
        # Create filler
        print("\n" + "#"*70)
        print("# PHASE 1: SCAN FORM")
        print("#"*70)
        
        filler = SmartFormFiller(browser.page, profile, role="TPM")
        fields = filler.scan_form()
        
        # Show detected fields
        print("\n📋 Detected fields:")
        for f in fields[:25]:
            status = "✅" if f.field_type.value != "unknown" else "❓"
            label = f.label[:40] if f.label else "(no label)"
            print(f"   {status} {f.field_key or f.element_id or f.selector}: {label}")
        
        if len(fields) > 25:
            print(f"   ... and {len(fields) - 25} more")
        
        # Pause
        print("\n" + "="*70)
        print("⏸️  Review detected fields above")
        print("Press Enter to start filling known fields...")
        print("="*70)
        input()
        
        # Fill known fields
        print("\n" + "#"*70)
        print("# PHASE 2: FILL KNOWN FIELDS")
        print("#"*70)
        
        filled = filler.fill_known_fields()
        
        print(f"\n✅ Filled {filled} fields automatically!")
        
        # Show what needs attention
        if filler.needs_user_input:
            print(f"\n❓ {len(filler.needs_user_input)} fields need your input:")
            for f in filler.needs_user_input[:10]:
                print(f"   - {f.label or f.selector}")
        
        if filler.needs_ai:
            print(f"\n🤖 {len(filler.needs_ai)} fields need AI generation:")
            for f in filler.needs_ai[:5]:
                print(f"   - {f.label or f.selector}")
        
        # Interactive mode?
        if filler.needs_user_input:
            print("\n" + "="*70)
            print("⏸️  Do you want to fill unknown fields interactively?")
            print("This will ask you for each unknown field and save to database.")
            print("="*70)
            choice = input("Enter (y)es to continue, anything else to skip: ").strip().lower()
            
            if choice in ("y", "yes"):
                print("\n" + "#"*70)
                print("# PHASE 3: INTERACTIVE FILL")
                print("#"*70)
                
                still_unknown = filler.process_unknown_fields(interactive=True)
                
                if still_unknown:
                    print(f"\n⚠️ {len(still_unknown)} fields still need attention")
        
        # Save database
        filler.save_database()
        
        # Summary
        summary = filler.get_summary()
        print("\n" + "="*70)
        print("📊 SUMMARY")
        print("="*70)
        print(f"   Total fields:      {summary['total_fields']}")
        print(f"   Filled:            {summary['filled']}")
        print(f"   Skipped:           {summary['skipped']}")
        print(f"   Needs user input:  {summary['needs_user_input']}")
        print(f"   Needs AI:          {summary['needs_ai']}")
        print("="*70)
        
        # Screenshot
        browser.screenshot("smart_fill_v2_result.png")
        
        print("\n⏸️ Check the browser. Press Enter to close...")
        input()
        
        print("\n✅ Done!")


if __name__ == "__main__":
    main()
