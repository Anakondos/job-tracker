# Browser automation configuration

from pathlib import Path

# Directories
BROWSER_DIR = Path(__file__).parent
SCREENSHOTS_DIR = BROWSER_DIR.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Timeouts (seconds)
PAGE_LOAD_TIMEOUT = 60
CLOUDFLARE_WAIT = 10
ELEMENT_TIMEOUT = 10

# Browser settings
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

BROWSER_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--no-sandbox',
    '--disable-dev-shm-usage',
]

# Anti-detection script
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5]
    });
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en']
    });
"""

# Common Apply button selectors (order matters - try first ones first)
APPLY_BUTTON_SELECTORS = [
    "text=Apply for this job",
    "text=Apply Now",
    "text=Apply",
    "button:has-text('Apply')",
    "a:has-text('Apply')",
    "[data-testid='apply-button']",
    ".apply-button",
    "#apply-button",
]

# Common form field patterns
FORM_FIELD_PATTERNS = {
    "name": ["name", "full_name", "fullname", "first_name", "firstname"],
    "email": ["email", "e-mail", "email_address"],
    "phone": ["phone", "telephone", "mobile", "phone_number"],
    "linkedin": ["linkedin", "linkedin_url", "linkedin_profile"],
    "resume": ["resume", "cv", "file", "attachment"],
    "cover_letter": ["cover_letter", "cover", "letter"],
}

# AI Configuration (for fallback)
AI_CONFIG = {
    "provider": "ollama",  # ollama (free) or claude (paid)
    "ollama_model": "llama3",
    "ollama_url": "http://localhost:11434",
    "claude_model": "claude-sonnet-4-20250514",
    "use_ai_threshold": 3,  # Try N times without AI before using AI
}
