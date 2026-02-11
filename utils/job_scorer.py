# utils/job_scorer.py
"""
JD Smart Matcher — keyword-based scoring without AI.
Based on jd_matcher.py by Anton Kondakov.

Scores each job against hardcoded profile on 5 dimensions:
  1. Role match (25 pts) — primary/secondary/avoid
  2. Domain match (25 pts) — strong/partial/weak/no-match
  3. Skills match (25 pts) — expert/proficient/basic/missing
  4. Location match (15 pts) — perfect/good/acceptable/difficult/poor
  5. Salary match (10 pts) — extracted from JD text

Plus red flags detection and negative signals.
Total: 0-100 → APPLY (75+) / CONSIDER (55-74) / SKIP (<55)

v2: Word boundary matching, negative context detection, improved red flags
"""

import re
import json
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, asdict

# ============================================================================
# CONFIGURATION - Anton's Profile
# ============================================================================

PROFILE = {
    "name": "Anton Kondakov",
    "years_experience": 15,
    "target_salary_min": 140000,
    "target_salary_max": 180000,
    "location": "Raleigh, NC",
    "work_authorization": "Green Card",
    "willing_to_relocate": ["Richmond VA", "Charlotte NC", "McLean VA"],
    "remote_preference": True,
}

# ============================================================================
# KEYWORDS CONFIGURATION
# ============================================================================

# Role Types - what positions to target
ROLE_KEYWORDS = {
    "primary": {  # Best fit roles
        "keywords": [
            "Technical Program Manager", "TPM", "Program Manager",
            "Product Manager", "Product Management", "Product Owner", "POPM",
            "Delivery Manager", "Engagement Manager",
            "Senior Technical Project Manager",
        ],
        "weight": 25
    },
    "secondary": {  # Good fit roles
        "keywords": [
            "Project Manager", "Sr. Project Manager", "Senior Project Manager",
            "Scrum Master", "Agile Coach", "Release Train Engineer", "RTE",
            "Business Analyst", "Domain Consultant",
        ],
        "weight": 18
    },
    "avoid": {  # Too junior or wrong direction
        "keywords": [
            "Associate Product Manager", "Junior", "Entry Level",
            "Software Engineer", "Developer", "Architect",
        ],
        "weight": -15
    }
}

# Domain Keywords - industries and areas of expertise
DOMAIN_KEYWORDS = {
    "strong_match": {  # Direct experience — specific financial terms
        "keywords": [
            "banking", "investment banking", "financial services", "fintech",
            "cash equities", "trading platform", "trade processing", "trade settlement",
            "regulatory reporting", "regulatory compliance", "MiFID II", "CAT reporting", "EMIR",
            "insurance industry", "insurance carrier", "london market", "reinsurance",
            "cloud migration", "GCP migration", "AWS migration", "platform modernization",
        ],
        "weight": 25
    },
    "partial_match": {  # Adjacent experience + generic finance terms
        "keywords": [
            "cards", "payments", "debit", "credit card",
            "retail banking", "consumer banking", "wealth management",
            "asset management", "investment operations",
            "enterprise", "fortune 500", "global",
            # Generic terms that need context (moved from strong to avoid false positives)
            "compliance", "regulatory", "audit", "transaction",
            "trading", "insurance", "financial",
        ],
        "weight": 15
    },
    "weak_match": {  # Limited/no experience
        "keywords": [
            "healthcare", "pharma", "medical", "life sciences",
            "telecom", "media", "retail", "e-commerce", "sporting goods", "consumer",
            "government", "public sector",
            "manufacturing", "industrial", "automotive", "aerospace",
            "energy", "utilities", "oil and gas",
        ],
        "weight": 5
    },
    "no_match": {  # Automatic disqualification areas
        "keywords": [
            "semiconductor manufacturing", "ASIC design", "chip design",
            "silicon wafer", "wafer fabrication", "GPU cluster",
            "ML research", "model training", "AI research", "deep learning research",
            "crypto", "cryptocurrency", "blockchain", "DeFi", "NFT", "staking", "web3",
            "Workday HCM", "Workday Security", "HRIS implementation",
            "Tableau developer", "Tableau expertise",
            "game development", "gaming studio",
        ],
        "weight": -30
    }
}

# Skills - technical and soft skills
SKILLS_KEYWORDS = {
    "expert": {  # Strong hands-on experience
        "keywords": [
            "Agile", "Scrum", "Kanban", "SAFe", "PI Planning",
            "Jira", "Confluence", "ServiceNow",
            "stakeholder management", "cross-functional",
            "regulatory compliance", "audit readiness",
            "cloud", "GCP", "AWS", "migration",
            "program management", "portfolio",
        ],
        "weight": 4  # per skill found
    },
    "proficient": {  # Good experience
        "keywords": [
            "CI/CD", "Jenkins", "GitHub Actions",
            "microservices", "API", "REST",
            "Python", "SQL", "data platform",
            "release management", "change management",
            "vendor management", "budget",
        ],
        "weight": 3
    },
    "basic": {  # Some exposure
        "keywords": [
            "Docker", "Terraform", "Kubernetes",
            "Power BI", "Tableau", "analytics",
            "Azure", "infrastructure",
        ],
        "weight": 2
    },
    "missing": {  # No experience - red flags if required
        "keywords": [
            "hands-on coding required", "write production code",
            "Kubernetes administrator", "K8s operator",
            "ServiceNow developer", "Flow Designer", "Glide scripting",
            "Charles River", "Aladdin", "Bloomberg AIM", "Simcorp",
            "CBAP required", "PMP required",
            "Salesforce expertise", "Salesforce product", "Sales Cloud",
            "Marketing Cloud", "hands-on Salesforce",
        ],
        "weight": -10
    }
}

# Location Scoring
LOCATION_KEYWORDS = {
    "perfect": {
        "keywords": ["Remote", "Raleigh", "RTP", "Research Triangle", "Durham", "NC"],
        "weight": 15
    },
    "good": {
        "keywords": ["Richmond", "Charlotte", "Virginia", "Hybrid", "Cary"],
        "weight": 12
    },
    "acceptable": {
        "keywords": ["McLean", "DC", "Washington", "Maryland", "Owings Mills"],
        "weight": 8
    },
    "difficult": {
        "keywords": ["New York", "NYC", "Chicago", "Boston", "Atlanta"],
        "weight": 4
    },
    "poor": {
        "keywords": ["San Francisco", "Seattle", "LA", "California", "West Coast", "Denver"],
        "weight": 0
    }
}

# Red Flags - automatic score reduction
RED_FLAGS = {
    "hard_requirements_missing": [
        "Security clearance required", "TS/SCI", "Top Secret", "Secret clearance",
        "US Citizen required", "citizenship required",
        "5+ years Kubernetes", "5+ years Docker",
        "hands-on coding", "write production code daily",
    ],
    "wrong_domain": [
        "semiconductor", "ASIC design", "chip design",
        "ML research", "AI research", "model training",
        "crypto", "blockchain", "DeFi",
        "Workday HCM", "Workday Security",
    ],
    "wrong_level": [
        "entry level", "junior", "associate level", "1-2 years experience",
        "new grad", "internship", "intern", "summer intern",
        "must be a student", "enrolled student", "undergraduate",
        "co-op", "trainee", "graduate program",
    ],
    "sponsorship_issues": [
        "no sponsorship", "will not sponsor", "cannot sponsor",
    ]
}

# Certifications - bonus points
CERTIFICATIONS = {
    "has": [
        "SAFe POPM", "SAFe Product Owner", "PSM I", "Scrum Master",
        "Google Cloud", "Cloud Architect", "Agile Program Manager",
    ],
    "preferred_missing": [
        "PMP", "CBAP", "CSM",
        "AWS Certified", "Azure Certified",
    ]
}


# ============================================================================
# SCORING FUNCTIONS
# ============================================================================

def extract_salary(text: str) -> Tuple[int, int]:
    """Extract salary range from JD text"""
    patterns = [
        r'\$(\d{1,3}),?(\d{3})\s*[-\u2013to]+\s*\$(\d{1,3}),?(\d{3})',
        r'\$(\d{2,3})k?\s*[-\u2013to]+\s*\$(\d{2,3})k',
        r'(\d{1,3}),?(\d{3})\s*[-\u2013to]+\s*(\d{1,3}),?(\d{3})\s*(?:USD|per year|annually)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) == 4:
                return (int(groups[0] + groups[1]), int(groups[2] + groups[3]))
            elif len(groups) == 2:
                return (int(groups[0]) * 1000, int(groups[1]) * 1000)
    return (0, 0)


def count_keyword_matches(text: str, keywords: List[str]) -> int:
    """Count how many keywords are found in text using word boundaries"""
    text_lower = text.lower()
    count = 0
    for kw in keywords:
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, text_lower):
            count += 1
    return count


def find_matching_keywords(text: str, keywords: List[str]) -> List[str]:
    """Return list of matched keywords using word boundaries"""
    text_lower = text.lower()
    matched = []
    for kw in keywords:
        pattern = r'\b' + re.escape(kw.lower()) + r'\b'
        if re.search(pattern, text_lower):
            matched.append(kw)
    return matched


def check_negative_context(text: str, keyword: str, negative_words: List[str]) -> bool:
    """Check if keyword appears in negative context (e.g., 'Remote: No')"""
    text_lower = text.lower()
    kw_lower = keyword.lower()

    for match in re.finditer(r'\b' + re.escape(kw_lower) + r'\b', text_lower):
        start = max(0, match.start() - 20)
        end = min(len(text_lower), match.end() + 20)
        context = text_lower[start:end]

        for neg in negative_words:
            if neg in context:
                return True  # Keyword is in negative context

    return False  # Keyword is NOT in negative context


@dataclass
class MatchResult:
    """Result of JD matching analysis"""
    score: int
    recommendation: str  # "APPLY", "CONSIDER", "SKIP"
    role_score: int
    domain_score: int
    skills_score: int
    location_score: int
    salary_score: int
    red_flags: List[str]
    matched_keywords: Dict[str, List[str]]
    salary_range: Tuple[int, int]
    analysis: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_jd(jd_text: str, job_title: str = "", company: str = "",
               location: str = "", geo_bucket: str = "") -> MatchResult:
    """
    Analyze a job description and return match score and recommendation.

    Can work with:
    - Full JD text (best accuracy)
    - Title + location only (basic scoring from pipeline metadata)
    """
    # Combine all available text for matching
    combined = f"{job_title} {company} {jd_text} {location}"
    text = combined.lower()
    matched_keywords = {}
    red_flags_found = []

    # =========================================
    # 1. ROLE SCORE (max 25)
    # =========================================
    role_score = 0

    primary_matches = find_matching_keywords(combined, ROLE_KEYWORDS["primary"]["keywords"])
    if primary_matches:
        role_score = ROLE_KEYWORDS["primary"]["weight"]
        matched_keywords["role_primary"] = primary_matches
    else:
        secondary_matches = find_matching_keywords(combined, ROLE_KEYWORDS["secondary"]["keywords"])
        if secondary_matches:
            role_score = ROLE_KEYWORDS["secondary"]["weight"]
            matched_keywords["role_secondary"] = secondary_matches

    avoid_matches = find_matching_keywords(combined, ROLE_KEYWORDS["avoid"]["keywords"])
    if avoid_matches:
        role_score += ROLE_KEYWORDS["avoid"]["weight"]
        red_flags_found.append(f"Junior/wrong role type: {', '.join(avoid_matches)}")

    # =========================================
    # 2. DOMAIN SCORE (max 25)
    # =========================================
    domain_score = 0

    strong_matches = find_matching_keywords(combined, DOMAIN_KEYWORDS["strong_match"]["keywords"])
    if strong_matches:
        domain_score = DOMAIN_KEYWORDS["strong_match"]["weight"]
        matched_keywords["domain_strong"] = strong_matches

    partial_matches = find_matching_keywords(combined, DOMAIN_KEYWORDS["partial_match"]["keywords"])
    if partial_matches and domain_score < 25:
        domain_score = max(domain_score, DOMAIN_KEYWORDS["partial_match"]["weight"])
        matched_keywords["domain_partial"] = partial_matches

    weak_matches = find_matching_keywords(combined, DOMAIN_KEYWORDS["weak_match"]["keywords"])
    if weak_matches and domain_score == 0:
        domain_score = DOMAIN_KEYWORDS["weak_match"]["weight"]
        matched_keywords["domain_weak"] = weak_matches

    no_match = find_matching_keywords(combined, DOMAIN_KEYWORDS["no_match"]["keywords"])
    if no_match:
        # Wrong domain overrides any positive domain match
        domain_score = DOMAIN_KEYWORDS["no_match"]["weight"]
        red_flags_found.append(f"Wrong domain: {', '.join(no_match)}")

    # =========================================
    # 3. SKILLS SCORE (max 25)
    # =========================================
    skills_score = 0

    expert_matches = find_matching_keywords(combined, SKILLS_KEYWORDS["expert"]["keywords"])
    skills_score += len(expert_matches) * SKILLS_KEYWORDS["expert"]["weight"]
    if expert_matches:
        matched_keywords["skills_expert"] = expert_matches

    proficient_matches = find_matching_keywords(combined, SKILLS_KEYWORDS["proficient"]["keywords"])
    skills_score += len(proficient_matches) * SKILLS_KEYWORDS["proficient"]["weight"]
    if proficient_matches:
        matched_keywords["skills_proficient"] = proficient_matches

    basic_matches = find_matching_keywords(combined, SKILLS_KEYWORDS["basic"]["keywords"])
    skills_score += len(basic_matches) * SKILLS_KEYWORDS["basic"]["weight"]
    if basic_matches:
        matched_keywords["skills_basic"] = basic_matches

    missing_matches = find_matching_keywords(combined, SKILLS_KEYWORDS["missing"]["keywords"])
    if missing_matches:
        for kw in missing_matches:
            idx = text.find(kw.lower())
            if idx >= 0 and "required" in text[max(0, idx - 50):idx + 50]:
                skills_score += SKILLS_KEYWORDS["missing"]["weight"]
                red_flags_found.append(f"Missing required skill: {kw}")

    skills_score = min(25, max(0, skills_score))

    # =========================================
    # 4. LOCATION SCORE (max 15)
    # =========================================
    location_score = 0
    negative_location_words = ["no", "not", "non", "without", "n/a"]

    # Use geo_bucket if available (from pipeline metadata)
    if geo_bucket:
        geo_scores = {
            "local": 15, "nc": 14, "neighbor": 12,
            "remote_usa": 13, "other_us": 4, "other": 0, "unknown": 6,
        }
        location_score = geo_scores.get(geo_bucket, 6)
    else:
        # Fall back to keyword matching with negative context detection
        loc_text = f"{location} {jd_text}"
        for level, config in LOCATION_KEYWORDS.items():
            matches = find_matching_keywords(loc_text, config["keywords"])
            # Filter out matches that are in negative context
            valid_matches = []
            for m in matches:
                if not check_negative_context(loc_text, m, negative_location_words):
                    valid_matches.append(m)

            if valid_matches:
                location_score = max(location_score, config["weight"])
                matched_keywords[f"location_{level}"] = valid_matches
                break  # Take highest match

    # Check for explicit "onsite only" or "no remote"
    if re.search(r'remote\s*:\s*no|onsite\s+only|on-site\s+only|no\s+remote', text):
        location_score = max(0, location_score - 10)
        red_flags_found.append("Location: Onsite only / No remote")

    # =========================================
    # 5. SALARY SCORE (max 10)
    # =========================================
    salary_range = extract_salary(jd_text)
    salary_score = 0

    if salary_range[1] > 0:
        actual_mid = (salary_range[0] + salary_range[1]) / 2
        if actual_mid >= PROFILE["target_salary_min"]:
            salary_score = 10
        elif actual_mid >= PROFILE["target_salary_min"] * 0.85:
            salary_score = 7
        elif actual_mid >= PROFILE["target_salary_min"] * 0.7:
            salary_score = 4
        else:
            salary_score = 0
            red_flags_found.append(f"Low salary: ${salary_range[0]:,} - ${salary_range[1]:,}")

    # =========================================
    # 6. RED FLAGS CHECK (word boundary matching)
    # =========================================
    for category, flags in RED_FLAGS.items():
        for flag in flags:
            pattern = r'\b' + re.escape(flag.lower()) + r'\b'
            if re.search(pattern, text):
                red_flags_found.append(f"{category}: {flag}")

    # =========================================
    # CALCULATE TOTAL SCORE
    # =========================================
    total_score = role_score + domain_score + skills_score + location_score + salary_score

    # Penalties: wrong domain is severe, missing skills moderate
    wrong_domain_flags = [f for f in red_flags_found if "Wrong domain" in f]
    missing_skill_flags = [f for f in red_flags_found if "Missing required" in f]
    penalty = len(wrong_domain_flags) * 25 + len(missing_skill_flags) * 10
    total_score = min(100, max(0, total_score - penalty))

    # =========================================
    # RECOMMENDATION
    # =========================================
    if total_score >= 75:
        recommendation = "APPLY"
    elif total_score >= 55:
        recommendation = "CONSIDER"
    else:
        recommendation = "SKIP"

    # Force SKIP for wrong domain — always disqualify
    if wrong_domain_flags:
        recommendation = "SKIP"
        total_score = min(total_score, 40)  # Cap score for wrong domain

    # =========================================
    # ANALYSIS TEXT
    # =========================================
    analysis = f"""
=== JD ANALYSIS: {job_title} at {company} ===

TOTAL SCORE: {total_score}/100 -> {recommendation}

Breakdown:
- Role Match:     {role_score}/25
- Domain Match:   {domain_score}/25
- Skills Match:   {skills_score}/25
- Location:       {location_score}/15
- Salary:         {salary_score}/10

Salary Range: ${salary_range[0]:,} - ${salary_range[1]:,}

Matched Keywords:
{json.dumps(matched_keywords, indent=2)}

Red Flags ({len(red_flags_found)}):
{chr(10).join(['- ' + f for f in red_flags_found]) if red_flags_found else '- None'}

Recommendation: {recommendation}
"""

    return MatchResult(
        score=total_score,
        recommendation=recommendation,
        role_score=role_score,
        domain_score=domain_score,
        skills_score=skills_score,
        location_score=location_score,
        salary_score=salary_score,
        red_flags=red_flags_found,
        matched_keywords=matched_keywords,
        salary_range=salary_range,
        analysis=analysis,
    )


# ============================================================================
# STANDALONE BATCH FUNCTIONS
# ============================================================================

def batch_analyze(jd_files: List[str]) -> List[Tuple[str, MatchResult]]:
    """Analyze multiple JD files and return sorted results"""
    results = []

    for filepath in jd_files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                jd_text = f.read()

            filename = Path(filepath).stem
            result = analyze_jd(jd_text, job_title=filename)
            results.append((filepath, result))
        except Exception as e:
            print(f"Error processing {filepath}: {e}")

    results.sort(key=lambda x: x[1].score, reverse=True)
    return results


def print_summary(results: List[Tuple[str, MatchResult]]):
    """Print summary of all analyzed JDs"""
    print("\n" + "=" * 80)
    print("JD MATCHING SUMMARY")
    print("=" * 80)

    apply_count = 0
    consider_count = 0
    skip_count = 0

    print("\n  APPLY (Score >= 75):")
    print("-" * 40)
    for filepath, result in results:
        if result.recommendation == "APPLY":
            print(f"  {Path(filepath).stem}: {result.score}/100")
            apply_count += 1

    print("\n  CONSIDER (Score 55-74):")
    print("-" * 40)
    for filepath, result in results:
        if result.recommendation == "CONSIDER":
            print(f"  {Path(filepath).stem}: {result.score}/100")
            consider_count += 1

    print("\n  SKIP (Score < 55):")
    print("-" * 40)
    for filepath, result in results:
        if result.recommendation == "SKIP":
            print(f"  {Path(filepath).stem}: {result.score}/100")
            if result.red_flags:
                print(f"     Red flags: {', '.join(result.red_flags[:2])}")
            skip_count += 1

    print("\n" + "=" * 80)
    print(f"TOTALS: {apply_count} APPLY | {consider_count} CONSIDER | {skip_count} SKIP")
    print("=" * 80)


# ============================================================================
# PIPELINE INTEGRATION
# ============================================================================

def score_job(job: dict, jd_text: str = "") -> dict:
    """
    Score a single pipeline job.
    Uses JD text if available, otherwise falls back to title+location.

    Returns dict compatible with pipeline: {kw_score, kw_tier, kw_recommendation, ...}
    """
    result = analyze_jd(
        jd_text=jd_text,
        job_title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        geo_bucket=job.get("geo_bucket", ""),
    )

    tier = "excellent" if result.score >= 75 else "good" if result.score >= 55 else "fair" if result.score >= 35 else "low"

    return {
        "kw_score": result.score,
        "kw_tier": tier,
        "kw_recommendation": result.recommendation,
        "kw_breakdown": {
            "role": result.role_score,
            "domain": result.domain_score,
            "skills": result.skills_score,
            "location": result.location_score,
            "salary": result.salary_score,
        },
        "kw_red_flags": result.red_flags,
        "kw_matched": result.matched_keywords,
        "kw_salary": list(result.salary_range),
        "kw_analysis": result.analysis,
    }


def score_jobs_batch(jobs: list, jd_dir: Path = None) -> list:
    """
    Score a batch of pipeline jobs. Loads cached JD text if available.
    Returns jobs with kw_score added, sorted by score descending.
    """
    if jd_dir is None:
        jd_dir = Path(__file__).parent.parent / "data" / "jd"

    for job in jobs:
        job_id = job.get("id", "")
        jd_text = ""

        # Try to load cached JD
        jd_file = jd_dir / f"{job_id}.txt"
        if jd_file.exists():
            try:
                jd_text = jd_file.read_text(encoding="utf-8")
            except Exception:
                pass

        result = score_job(job, jd_text)
        job.update(result)

    jobs.sort(key=lambda j: j.get("kw_score", 0), reverse=True)
    return jobs


# ============================================================================
# MAIN - Example Usage
# ============================================================================

if __name__ == "__main__":
    sample_jd = """
    Senior Technical Program Manager - Financial Services
    Location: Richmond, VA (Hybrid)
    Salary: $150,000 - $180,000

    We are looking for a Senior TPM to lead cloud migration initiatives
    in our investment banking division.

    Requirements:
    - 10+ years program management experience
    - Experience with GCP or AWS cloud platforms
    - SAFe or Scrum certification preferred
    - Financial services background required
    - Strong stakeholder management skills
    - Experience with regulatory compliance (MiFID, SOX)

    Nice to have:
    - Jira, Confluence experience
    - CI/CD pipeline knowledge
    """

    result = analyze_jd(sample_jd, "Senior TPM - Financial Services", "Example Bank")
    print(result.analysis)
