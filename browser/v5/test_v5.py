#!/usr/bin/env python3
"""
Test V5 Form Filler

Usage:
    # First, start Chrome with debugging:
    ./browser/start-chrome-debug.sh
    
    # Then run this test:
    python browser/v5/test_v5.py
    
    # Or with specific URL:
    python browser/v5/test_v5.py "https://job-boards.greenhouse.io/..."
"""

import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from browser.v5 import FormFillerV5
from browser.v5.browser_manager import BrowserManager, BrowserMode, print_chrome_instructions


def test_connection():
    """Test browser connection."""
    print("\n" + "="*60)
    print("TEST 1: Browser Connection")
    print("="*60)
    
    try:
        with BrowserManager(mode=BrowserMode.CDP) as browser:
            print(f"✅ Connected to Chrome")
            print(f"   Current URL: {browser.current_url()}")
            print(f"   Title: {browser.title()}")
            return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print_chrome_instructions()
        return False


def test_preflight(url: str):
    """Test pre-flight analysis."""
    print("\n" + "="*60)
    print("TEST 2: Pre-flight Analysis")
    print("="*60)
    
    filler = FormFillerV5(browser_mode=BrowserMode.CDP)
    report = filler.analyze(url)
    
    print(report.summary())
    return report


def test_fill(url: str):
    """Test interactive fill."""
    print("\n" + "="*60)
    print("TEST 3: Interactive Fill")
    print("="*60)
    
    filler = FormFillerV5(browser_mode=BrowserMode.CDP)
    
    from browser.v5.engine import FillMode
    report = filler.fill(url, mode=FillMode.INTERACTIVE)
    
    print(report.summary())
    return report


def main():
    # Default test URL (Coinbase Greenhouse)
    url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://job-boards.greenhouse.io/embed/job_app?token=7404427&for=coinbase&gh_jid=7404427"
    
    print("\n" + "="*70)
    print("🧪 V5 FORM FILLER TEST SUITE")
    print("="*70)
    print(f"Test URL: {url[:60]}...")
    print("="*70)
    
    # Test 1: Connection
    if not test_connection():
        print("\n❌ Cannot proceed without browser connection.")
        print("   Please start Chrome with: ./browser/start-chrome-debug.sh")
        return
    
    # Test 2: Pre-flight
    print("\n\nProceed with pre-flight analysis? (y/n)")
    if input("> ").strip().lower() == 'y':
        report = test_preflight(url)
        
        # Test 3: Fill (only if pre-flight ok)
        if report.ready_fields > 0:
            print("\n\nProceed with form filling? (y/n)")
            if input("> ").strip().lower() == 'y':
                test_fill(url)
    
    print("\n✅ Tests complete!")


if __name__ == "__main__":
    main()
