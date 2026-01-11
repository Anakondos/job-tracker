# V5 Form Filler - Setup

## Requirements

```bash
pip install anthropic playwright
playwright install chromium
```

## API Key Setup

### Option 1: Environment Variable (Recommended)
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Add to your `~/.zshrc` or `~/.bash_profile` for persistence.

### Option 2: Config File
```bash
cp browser/v5/config/api_keys.json.template browser/v5/config/api_keys.json
# Edit and add your key
```

### Option 3: Home Directory
```bash
mkdir -p ~/.anthropic
echo "sk-ant-..." > ~/.anthropic/api_key
```

## Usage

### 1. Start Chrome with Debugging
```bash
./browser/start-chrome-debug.sh
```

This opens Chrome with your regular profile (all logins preserved).

### 2. Run Form Filler

**Pre-flight Analysis (no fill):**
```bash
python browser/v5/engine.py "https://job-url..." preflight
```

**Interactive Fill:**
```bash
python browser/v5/engine.py "https://job-url..." interactive
```

**From Python:**
```python
from browser.v5 import FormFillerV5
from browser.v5.engine import FillMode
from browser.v5.browser_manager import BrowserMode

# Analyze form
filler = FormFillerV5(browser_mode=BrowserMode.CDP)
report = filler.analyze("https://...")
print(report.summary())

# Fill form
report = filler.fill("https://...", mode=FillMode.INTERACTIVE)
```

## Architecture

```
V5 FORM FILLER
│
├── LAYER 1: DETECTION
│   ├── HTML analysis (tag, type, attributes)
│   ├── ARIA analysis (role, haspopup)
│   ├── Probe (click to observe)
│   └── Claude Vision (screenshot analysis)
│
├── LAYER 2: RESOLUTION  
│   ├── Learned DB (saved answers)
│   ├── Profile (personal data)
│   ├── Yes/No defaults
│   ├── Demographic defaults
│   ├── Claude AI (custom questions)
│   └── Human input (fallback)
│
├── LAYER 3: INPUT
│   ├── text/email → el.fill()
│   ├── select → el.select_option()
│   ├── autocomplete → type + click option
│   ├── checkbox → el.click()
│   └── file → el.set_input_files()
│
├── LAYER 4: VALIDATION
│   ├── Read back value
│   ├── Check aria-invalid
│   └── Claude Vision verification
│
└── LAYER 5: LEARNING
    └── Save successful fills
```

## Costs

Claude API is pay-per-use:
- Text generation: ~$0.003 per 1K tokens
- Vision: ~$0.003 per image

Typical form fill: ~$0.01-0.05
