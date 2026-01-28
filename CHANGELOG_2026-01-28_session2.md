# Job Tracker - Changelog 2026-01-28 (Session 2)

## Summary
V6 Form Filler AI integration + Multi-machine sync fixes

---

## 1. V6 AI Integration

### Added AI Helper (`browser/v6/ai_helper.py`)
- New file with Claude API integration for form filling
- Uses `claude-sonnet-4-20250514` model for fast responses
- Methods:
  - `get_answer(question, options, profile_context)` - Get answer for form question
  - `match_option(answer, options)` - Find best matching dropdown option

### Updated V6 Engine (`browser/v6/engine.py`)
- Added AI fallback cascade: Pattern → Special cases → AI
- Skip `question_*-label` and `question_*-description` elements
- Added 2s wait in `find_frame()` for page load
- AI asks Claude for unknown questions when patterns don't match

### API Key Configuration
- Key stored in: `browser/v5/config/api_keys.json`
- V6 reads from same location as V5

### Test Results with AI
| Company | Fields | Filled | Status |
|---------|--------|--------|--------|
| Coinbase | 29 | 29 | ✅ |
| PagerDuty | 7 | 7 | ✅ |
| Abnormal Security | 23 | 22 | ✅ (1 optional skip) |

---

## 2. Multi-Machine Sync Fixes

### Problem
- Both laptops (`anton` and `antonkondakov`) writing to same iCloud files
- Daemon running on both machines caused JSON corruption
- Hardcoded paths `/Users/anton/` didn't work on second laptop

### Universal Paths (`main.py`)
Added helper functions at top of file:
```python
def get_icloud_path() -> Path:
    return Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"

def get_ai_projects_path() -> Path:
    return get_icloud_path() / "Dev" / "AI_projects"

def get_gold_cv_path() -> Path:
    return get_ai_projects_path() / "Gold CV"

# Pre-computed paths
ICLOUD_PATH = get_icloud_path()
AI_PROJECTS_PATH = get_ai_projects_path()
GOLD_CV_PATH = get_gold_cv_path()
```

### V6 Engine Paths (`browser/v6/engine.py`)
```python
ICLOUD_PATH = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")
AI_PROJECTS_PATH = os.path.join(ICLOUD_PATH, "Dev", "AI_projects")
GOLD_CV_PATH = os.path.join(AI_PROJECTS_PATH, "Gold CV")

CV_PATH = os.path.join(GOLD_CV_PATH, "CV_Anton_Kondakov_Product Manager.pdf")
COVER_LETTER_PATH = os.path.join(GOLD_CV_PATH, "Cover_Letter_Anton_Kondakov_ProductM.docx")
```

### Daemon Lock System (`main.py`)
Prevents multiple machines from running daemon simultaneously:

```python
DAEMON_LOCK_FILE = Path("data/daemon.lock")

def get_machine_id() -> str:
    return f"{getpass.getuser()}@{socket.gethostname()}"

def check_daemon_lock() -> dict | None:
    # Returns lock info if locked, None if free
    # Lock expires after 10 minutes (stale protection)

def acquire_daemon_lock() -> tuple[bool, str]:
    # Returns (success, message)
    # Fails if locked by different machine

def release_daemon_lock():
    # Removes lock file if we own it
```

### Lock File Format (`data/daemon.lock`)
```json
{
  "machine": "anton@Mac.lan",
  "timestamp": "2026-01-28T02:37:32.037692+00:00",
  "pid": 75246
}
```

### Daemon Default State
Changed from auto-start to disabled by default:
```python
DAEMON_STATUS = {
    "enabled": False,  # Was True - now disabled to prevent conflicts
    ...
}
```

---

## 3. UI Updates (`static/index.html`)

### Lock Status Display
Shows when daemon locked by another machine:
```javascript
if (status.locked_by) {
  text.textContent = `🔒 Daemon locked by: ${status.locked_by}`;
  indicator.style.background = "#f59e0b";  // amber
}
```

### Refresh Protection
Prevents refresh if daemon locked:
```javascript
async function backgroundRefresh() {
  // Check if daemon is locked by another machine
  const status = await (await fetch("/daemon/status")).json();
  if (status.locked_by) {
    alert(`⚠️ Daemon is locked by another machine:\n\n${status.locked_by}`);
    return;
  }
  // ... continue refresh
}
```

---

## 4. Fixed Symlinks

### job_status.json
- Was: broken symlink `job_status 2.json` → wrong path
- Now: proper symlink `job_status.json` → `../job-tracker/job_status.json`
- Both DEV and PROD share same status file

---

## 5. Git Commits

```
df7c4e3 - V6: Add AI fallback for unknown questions
e1d6685 - Fix: Universal paths + disable daemon auto-start  
c418b33 - UI: Show daemon lock status + prevent refresh if locked
```

---

## 6. How to Start Server (Both Machines)

```bash
cd ~/Library/Mobile\ Documents/com~apple~CloudDocs/Dev/AI_projects/job-tracker-dev

# Stop old server
pkill -f "uvicorn.*8001"

# Start server
python3 -m uvicorn main:app --host 0.0.0.0 --port 8001 &

# Open app
open "http://127.0.0.1:8001/static/index.html"
```

---

## 7. Multi-Machine Usage Rules

1. **Data syncs automatically** via iCloud (companies, jobs, statuses)
2. **Daemon** - enable only on ONE machine at a time
3. **Lock prevents conflicts** - UI shows which machine has daemon running
4. **Lock expires** after 10 minutes if machine crashes/disconnects

---

## 8. Memory Updates

Added to Claude memory:
- `Job-tracker app URL: http://127.0.0.1:8001/static/index.html (port 8001, not 8000)`

---

## 9. Files Modified

### New Files
- `browser/v6/ai_helper.py` - AI integration for form filling

### Modified Files
- `main.py` - Universal paths, daemon lock, disabled auto-start
- `browser/v6/engine.py` - AI fallback, universal paths, iframe detection fixes
- `static/index.html` - Lock status display, refresh protection

### Fixed/Removed
- `job_status 2.json` - Removed broken symlink
- `job_status.json` - Fixed symlink to shared file
