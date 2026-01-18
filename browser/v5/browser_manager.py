"""
Browser Manager V5 - Connect to Real Chrome with CDP

Methods to connect to your browser:
1. CDP (Chrome DevTools Protocol) - Connect to running Chrome
2. Persistent Context - Playwright manages profile with saved state
3. Cookie Import - Export cookies from Chrome, import to Playwright

This module uses CDP for maximum compatibility with your existing logins.
"""

import os
import json
import time
import shutil
import tempfile
import subprocess
import socket
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright


class BrowserMode(Enum):
    """Browser connection modes"""
    CDP = "cdp"                    # Connect to running Chrome via DevTools Protocol
    PERSISTENT = "persistent"      # Playwright manages persistent profile
    FRESH = "fresh"               # Clean browser (no cookies)


@dataclass
class BrowserConfig:
    """Browser configuration"""
    mode: BrowserMode = BrowserMode.CDP
    cdp_url: str = "http://localhost:9222"
    chrome_profile_path: Optional[str] = None
    headless: bool = False
    viewport_width: int = 1400
    viewport_height: int = 900
    slow_mo: int = 50  # ms between actions
    use_default_profile: bool = False  # Use main Chrome profile (requires closing Chrome first)


# Default Chrome profile path on macOS
DEFAULT_CHROME_PROFILE = os.path.expanduser(
    "~/Library/Application Support/Google/Chrome"
)


class BrowserManager:
    """
    Manages browser connection with multiple modes.
    
    Usage:
        # Mode 1: Connect to running Chrome (recommended)
        # First, start Chrome with: 
        # /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
        
        with BrowserManager(mode=BrowserMode.CDP) as browser:
            browser.goto("https://greenhouse.io/...")
            # Your Chrome logins are available!
        
        # Mode 2: Persistent context (Playwright manages profile)
        with BrowserManager(mode=BrowserMode.PERSISTENT) as browser:
            browser.goto("https://...")
            # Cookies saved between sessions
        
        # Mode 3: Fresh browser
        with BrowserManager(mode=BrowserMode.FRESH) as browser:
            browser.goto("https://...")
    """
    
    def __init__(
        self,
        mode: BrowserMode = BrowserMode.CDP,
        config: Optional[BrowserConfig] = None
    ):
        self.mode = mode
        self.config = config or BrowserConfig(mode=mode)
        
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._temp_profile_dir: Optional[str] = None
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def start(self) -> "BrowserManager":
        """Start browser with configured mode."""
        self.playwright = sync_playwright().start()
        
        if self.mode == BrowserMode.CDP:
            self._start_cdp()
        elif self.mode == BrowserMode.PERSISTENT:
            self._start_persistent()
        else:
            self._start_fresh()
        
        return self
    
    def _is_cdp_available(self) -> bool:
        """Check if Chrome is listening on CDP port."""
        try:
            host = self.config.cdp_url.replace("http://", "").replace("https://", "")
            hostname, port = host.split(":")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex((hostname, int(port)))
            sock.close()
            return result == 0
        except:
            return False
    
    def _start_chrome_with_debug(self) -> bool:
        """Start Chrome with remote debugging enabled."""
        port = self.config.cdp_url.split(":")[-1]
        
        # Check if Chrome is already running with CDP
        if self._is_cdp_available():
            print("   ✅ Chrome already running with CDP")
            return True
        
        print(f"🚀 Starting Chrome with remote debugging on port {port}...")
        
        # macOS Chrome path
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        
        if not os.path.exists(chrome_path):
            print("   ❌ Chrome not found at default location")
            return False
        
        # Decide which profile to use
        if self.config.use_default_profile:
            # Use main Chrome profile - has all your logins!
            # But requires closing regular Chrome first
            chrome_running = self._is_chrome_running()
            if chrome_running:
                print("   ⚠️  Regular Chrome is running. Closing it to use your profile...")
                # Use osascript for graceful quit on macOS
                subprocess.run(["osascript", "-e", 'quit app "Google Chrome"'], capture_output=True)
                time.sleep(4)  # Wait for Chrome to fully close
            
            profile_path = None  # Use default Chrome profile
            print("   📁 Using your main Chrome profile (with logins)")
        else:
            # Use separate debug profile
            profile_path = os.path.expanduser("~/.chrome-debug-profile")
        
        # Build Chrome args
        chrome_args = [
            chrome_path,
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        
        if profile_path:
            chrome_args.append(f"--user-data-dir={profile_path}")
            print(f"   📁 Profile: {profile_path}")
        
        # Start Chrome with debugging
        try:
            subprocess.Popen(
                chrome_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Wait for Chrome to start
            for i in range(15):
                time.sleep(1)
                if self._is_cdp_available():
                    print(f"   ✅ Chrome started successfully")
                    if profile_path and not self.config.use_default_profile:
                        print(f"   💡 Note: Separate profile. Log in to sites if needed.")
                    return True
                print(f"   ⏳ Waiting for Chrome... ({i+1}/15)")
            
            print("   ❌ Chrome didn't start in time")
            return False
            
        except Exception as e:
            print(f"   ❌ Failed to start Chrome: {e}")
            return False
    
    def _is_chrome_running(self) -> bool:
        """Check if any Chrome process is running."""
        try:
            result = subprocess.run(["pgrep", "-f", "Google Chrome"], capture_output=True)
            return result.returncode == 0
        except:
            return False
    
    def _start_cdp(self):
        """Connect to running Chrome via CDP, auto-start if needed."""
        print(f"🔌 Connecting to Chrome via CDP at {self.config.cdp_url}...")
        
        # Auto-start Chrome if not running
        if not self._is_cdp_available():
            if not self._start_chrome_with_debug():
                raise RuntimeError("Could not start Chrome with debugging")
        
        try:
            self.browser = self.playwright.chromium.connect_over_cdp(
                self.config.cdp_url
            )
            
            # Get existing context or create new
            contexts = self.browser.contexts
            if contexts:
                self.context = contexts[0]
                print(f"   ✅ Using existing context with {len(self.context.pages)} pages")
            else:
                self.context = self.browser.new_context(
                    viewport={
                        "width": self.config.viewport_width,
                        "height": self.config.viewport_height
                    }
                )
            
            # Get existing page or create new
            if self.context.pages:
                self.page = self.context.pages[0]
            else:
                self.page = self.context.new_page()
            
            print(f"   ✅ Connected to Chrome (CDP mode)")
            print(f"   📄 Current URL: {self.page.url}")
            
        except Exception as e:
            print(f"   ❌ CDP connection failed: {e}")
            raise
    
    def _start_persistent(self):
        """Start with persistent profile (cookies saved)."""
        profile_dir = self.config.chrome_profile_path or os.path.expanduser(
            "~/.job-tracker-browser"
        )
        
        print(f"📁 Using persistent profile: {profile_dir}")
        
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=self.config.headless,
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height
            },
            slow_mo=self.config.slow_mo if not self.config.headless else 0,
            args=["--disable-blink-features=AutomationControlled"],
        )
        
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        print(f"   ✅ Browser started (persistent mode)")
    
    def _start_fresh(self):
        """Start clean browser without cookies."""
        print("🆕 Starting fresh browser...")
        
        self.browser = self.playwright.chromium.launch(
            headless=self.config.headless,
            slow_mo=self.config.slow_mo if not self.config.headless else 0,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        
        self.context = self.browser.new_context(
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height
            },
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        
        # Anti-detection script
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)
        
        self.page = self.context.new_page()
        print(f"   ✅ Browser started (fresh mode)")
    
    def close(self):
        """Close browser and cleanup."""
        if self.mode == BrowserMode.CDP:
            # Don't close the browser in CDP mode - just disconnect
            print("🔌 Disconnecting from Chrome (browser stays open)")
        else:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            print("✅ Browser closed")
        
        if self.playwright:
            self.playwright.stop()
        
        # Cleanup temp profile
        if self._temp_profile_dir and os.path.exists(self._temp_profile_dir):
            shutil.rmtree(self._temp_profile_dir, ignore_errors=True)
    
    # ─────────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────────
    
    def goto(self, url: str, wait_until: str = "networkidle", timeout: int = 30000) -> bool:
        """Navigate to URL."""
        print(f"\n🌐 Opening: {url[:80]}...")
        try:
            self.page.goto(url, wait_until=wait_until, timeout=timeout)
            time.sleep(1)
            print(f"   📄 Title: {self.page.title()[:60]}")
            return True
        except Exception as e:
            print(f"   ❌ Navigation failed: {e}")
            return False
    
    def new_tab(self, url: Optional[str] = None) -> Page:
        """Open new tab."""
        page = self.context.new_page()
        if url:
            page.goto(url)
        return page
    
    def current_url(self) -> str:
        """Get current URL."""
        return self.page.url
    
    def title(self) -> str:
        """Get page title."""
        return self.page.title()
    
    # ─────────────────────────────────────────────────────────────────
    # Screenshots
    # ─────────────────────────────────────────────────────────────────
    
    def screenshot(self, path: Optional[str] = None, full_page: bool = False) -> Path:
        """Take screenshot."""
        if path is None:
            screenshots_dir = Path(__file__).parent.parent / "screenshots"
            screenshots_dir.mkdir(exist_ok=True)
            path = screenshots_dir / f"screenshot_{int(time.time())}.png"
        
        self.page.screenshot(path=str(path), full_page=full_page)
        return Path(path)
    
    def screenshot_element(self, selector: str, path: Optional[str] = None) -> Optional[Path]:
        """Screenshot specific element."""
        try:
            el = self.page.query_selector(selector)
            if el:
                if path is None:
                    screenshots_dir = Path(__file__).parent.parent / "screenshots"
                    screenshots_dir.mkdir(exist_ok=True)
                    path = screenshots_dir / f"element_{int(time.time())}.png"
                el.screenshot(path=str(path))
                return Path(path)
        except Exception as e:
            print(f"   ⚠️ Element screenshot failed: {e}")
        return None
    
    # ─────────────────────────────────────────────────────────────────
    # Cookie Management
    # ─────────────────────────────────────────────────────────────────
    
    def export_cookies(self, path: Optional[str] = None) -> Path:
        """Export cookies to JSON file."""
        cookies = self.context.cookies()
        
        if path is None:
            path = Path(__file__).parent / "cookies.json"
        
        with open(path, "w") as f:
            json.dump(cookies, f, indent=2)
        
        print(f"📦 Exported {len(cookies)} cookies to {path}")
        return Path(path)
    
    def import_cookies(self, path: str):
        """Import cookies from JSON file."""
        with open(path) as f:
            cookies = json.load(f)
        
        self.context.add_cookies(cookies)
        print(f"📦 Imported {len(cookies)} cookies")
    
    def clear_cookies(self):
        """Clear all cookies."""
        self.context.clear_cookies()
        print("🗑️ Cookies cleared")
    
    # ─────────────────────────────────────────────────────────────────
    # Wait helpers
    # ─────────────────────────────────────────────────────────────────
    
    def wait_for_selector(self, selector: str, timeout: int = 10000) -> bool:
        """Wait for element to appear."""
        try:
            self.page.wait_for_selector(selector, timeout=timeout)
            return True
        except:
            return False
    
    def wait_for_navigation(self, timeout: int = 30000):
        """Wait for navigation to complete."""
        self.page.wait_for_load_state("networkidle", timeout=timeout)
    
    def wait_for_stable(self, timeout: float = 2.0):
        """Wait for page to stabilize (no new elements appearing)."""
        prev_count = 0
        start = time.time()
        while time.time() - start < timeout:
            count = len(self.page.query_selector_all("input, select, textarea, button"))
            if count == prev_count:
                return
            prev_count = count
            time.sleep(0.3)
    
    # ─────────────────────────────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────────────────────────────
    
    def scroll(self, pixels: int = 500):
        """Scroll down by pixels."""
        self.page.evaluate(f"window.scrollBy(0, {pixels})")
        time.sleep(0.3)
    
    def scroll_to_bottom(self):
        """Scroll to page bottom."""
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
    
    def scroll_to_top(self):
        """Scroll to page top."""
        self.page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.3)
    
    def highlight_element(self, selector: str, color: str = "red"):
        """Highlight element with colored border."""
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) {{
                    el.style.outline = '3px solid {color}';
                    el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                }}
            """)
        except:
            pass
    
    def unhighlight_element(self, selector: str):
        """Remove highlight from element."""
        try:
            self.page.evaluate(f"""
                const el = document.querySelector('{selector}');
                if (el) el.style.outline = '';
            """)
        except:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Helper to start Chrome with debugging
# ═══════════════════════════════════════════════════════════════════════════

def start_chrome_debug(port: int = 9222) -> str:
    """
    Returns command to start Chrome with debugging enabled.
    User should run this manually or via script.
    """
    return f'''/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
    --remote-debugging-port={port} \\
    --user-data-dir="$HOME/ChromeDebug"'''


def print_chrome_instructions():
    """Print instructions for starting Chrome with debugging."""
    print("""
╔══════════════════════════════════════════════════════════════════════════╗
║  HOW TO USE REAL CHROME WITH YOUR LOGINS                                ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  OPTION 1: Start Chrome with debugging (one-time setup)                  ║
║  ────────────────────────────────────────────────────────────────────    ║
║                                                                          ║
║  Run in Terminal:                                                        ║
║                                                                          ║
║  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\      ║
║      --remote-debugging-port=9222                                        ║
║                                                                          ║
║  Then in Python:                                                         ║
║                                                                          ║
║  from browser.v5 import BrowserManager, BrowserMode                      ║
║  with BrowserManager(mode=BrowserMode.CDP) as browser:                   ║
║      browser.goto("https://greenhouse.io/...")                           ║
║      # Your Chrome logins are available!                                 ║
║                                                                          ║
║  ────────────────────────────────────────────────────────────────────    ║
║                                                                          ║
║  OPTION 2: Create a shortcut/alias                                       ║
║  ────────────────────────────────────────────────────────────────────    ║
║                                                                          ║
║  Add to ~/.zshrc:                                                        ║
║                                                                          ║
║  alias chrome-debug='/Applications/Google\\ Chrome.app/Contents/MacOS/\\║
║  Google\\ Chrome --remote-debugging-port=9222'                           ║
║                                                                          ║
║  Then just run: chrome-debug                                             ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
""")


if __name__ == "__main__":
    print_chrome_instructions()
