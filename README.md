# Job Tracker

Intelligent job aggregator for Product/Program Management roles with AI-powered matching.

## Features

- 🔍 **Smart Role Matching**: Hybrid rule-based + AI classification
- 💾 **Job Caching**: 6-hour TTL cache for fast performance
- 📊 **Local Storage**: Saves jobs when status changes (Applied, Interested, etc.)
- 🌍 **Location Filtering**: NC focus with remote options
- 🏢 **Industry Filters**: Fintech, Big Tech, SaaS, and more
- 🎯 **Target Roles**: PM, TPM, Program Manager, Product Owner, Project Manager, Scrum Master

## Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install packages
pip install -r requirements.txt
```

### 2. Configure (Optional AI)

```bash
# Copy example env file
cp .env.example .env

# Edit .env and add your Anthropic API key (optional)
# Get key at: https://console.anthropic.com/
ANTHROPIC_API_KEY=sk-ant-api03-xxx...
```

**Without API key**: Uses rule-based classification (free, works great!)  
**With API key**: Adds AI for ambiguous cases (~$5-10/month)

### 3. Run Application

```bash
uvicorn main:app --reload
```

Open: http://localhost:8000

## API Endpoints

### Get Jobs
```http
GET /jobs?states=NC,VA&include_remote_usa=true&role_filter=product
```

**Parameters:**
- `states`: Comma-separated state codes (NC, VA, etc.)
- `include_remote_usa`: Include Remote-USA positions
- `role_filter`: all | product | tpm_program | project | other
- `ats_filter`: all | greenhouse | lever | smartrecruiters
- `company_filter`: Search by company name
- `search`: Search title/location

### Update Job Status
```http
POST /job_status
{
  "profile": "all",
  "job_key": "https://job-url",
  "status": "Applied"
}
```

**Statuses**: New, Applied, Interested, Rejected

## Configuration

### `config/roles.json`
Defines target roles with keywords for matching.

### `config/industries.json`
Industry categories for filtering.

### `config/settings.json`
```json
{
  "cache": {
    "enabled": true,
    "ttl_hours": 6
  },
  "ai": {
    "enabled": false,  // Auto-enabled if API key present
    "use_for_ambiguous_only": true
  }
}
```

## How It Works

### 1. Job Classification (Hybrid Approach)

```python
# Step 1: Rule-based (fast, free)
if exact_match(title):
    return high_confidence_match

# Step 2: Keyword matching
if keywords_match(title, description):
    return medium_confidence_match

# Step 3: AI (if enabled and ambiguous)
if ambiguous and AI_ENABLED:
    return ai_classification(title, description)
```

### 2. Job Categories

**Primary Roles** (show first):
- Product Manager
- Technical Program Manager
- Program Manager
- Product Owner
- Project Manager
- Scrum Master

**Adjacent Roles** (separate section):
- Director of Product/Program
- Senior Product Analyst
- Product Operations

**Skip** (filtered out):
- Engineers, Designers, QA, Sales, Non-IT

### 3. Local Storage

When you change status from "New" to anything else:
- ✅ Full job data saved locally
- ✅ Persists even if company closes posting
- ✅ Track your applications

## Data Files

```
job-tracker/
├── config/
│   ├── roles.json          # Role definitions
│   ├── industries.json     # Industry categories
│   └── settings.json       # App configuration
├── data/
│   └── companies.json      # Master company list
├── cache/
│   └── jobs_all.json       # Cached job listings (auto-generated)
├── job_status.json         # Job statuses (auto-generated)
└── job_storage_local.json  # Local job storage (auto-generated)
```

## Adding Companies

Edit `data/companies.json`:

```json
{
  "id": "stripe",
  "name": "Stripe",
  "ats": "greenhouse",
  "board_url": "https://boards.greenhouse.io/stripe",
  "tags": ["fintech", "payments"],
  "priority": 10,
  "hq_state": "CA"
}
```

**Supported ATS:**
- `greenhouse`: Greenhouse.io
- `lever`: Lever.co
- `smartrecruiters`: SmartRecruiters
- `workday`: Workday (partial support)

## Cost Estimate (with AI)

**Rule-based only**: FREE  
**With AI**:
- First full parse (1000 jobs): ~$5
- Daily updates (50-100 new jobs): ~$0.50
- **Monthly**: ~$10-15

**Recommendation**: Start with rules, add AI later if needed.

## Development

### Run Tests
```bash
# Test role classifier
python utils/role_classifier_rules.py

# Test AI classifier (requires API key)
python utils/role_classifier_ai.py

# Test cache manager
python utils/cache_manager.py

# Test storage
python utils/job_storage.py
```

### Add New Role

Edit `config/roles.json`:
```json
{
  "id": "delivery_manager",
  "name": "Delivery Manager",
  "aliases": ["Delivery Manager", "Delivery Lead"],
  "keywords_title": ["delivery manager", "delivery lead"],
  "keywords_description": ["agile delivery", "team delivery"],
  "priority": 7,
  "category": "primary"
}
```

## Troubleshooting

**"No ANTHROPIC_API_KEY found"**
- Normal! AI is optional. Rules work great without it.

**"Cache expired"**
- Expected after 6 hours. Click refresh to update.

**"Company parsing failed"**
- Check if ATS URL is correct
- Some companies block automated requests

**Jobs not matching**
- Check `config/roles.json` keywords
- Enable AI for better matching
- Add custom keywords for your use case

## License

MIT
