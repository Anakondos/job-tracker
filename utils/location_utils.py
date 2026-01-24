"""
Location utilities for Job Tracker

Parses and normalizes location from job title or raw location string.
"""

import re
from typing import Dict, Optional, List

# US State mappings
US_STATES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
    'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV',
    'wisconsin': 'WI', 'wyoming': 'WY', 'district of columbia': 'DC'
}

# Reverse mapping: abbreviation -> full name
STATE_NAMES = {v: k.title() for k, v in US_STATES.items()}

# Common US cities for detection
COMMON_CITIES = {
    'new york': 'NY', 'los angeles': 'CA', 'chicago': 'IL', 'houston': 'TX',
    'phoenix': 'AZ', 'philadelphia': 'PA', 'san antonio': 'TX', 'san diego': 'CA',
    'dallas': 'TX', 'san jose': 'CA', 'austin': 'TX', 'jacksonville': 'FL',
    'san francisco': 'CA', 'seattle': 'WA', 'denver': 'CO', 'boston': 'MA',
    'atlanta': 'GA', 'miami': 'FL', 'raleigh': 'NC', 'charlotte': 'NC',
    'portland': 'OR', 'las vegas': 'NV', 'detroit': 'MI', 'minneapolis': 'MN',
    'tampa': 'FL', 'orlando': 'FL', 'pittsburgh': 'PA', 'cleveland': 'OH',
    'richmond': 'VA', 'salt lake city': 'UT', 'nashville': 'TN', 'durham': 'NC',
    'cary': 'NC', 'chapel hill': 'NC', 'wake forest': 'NC', 'morrisville': 'NC',
}


def extract_location_from_text(text: str) -> Dict:
    """
    Extract location info from text (title or raw location).
    
    Returns dict with:
        - raw: original text
        - city: detected city
        - state: state abbreviation
        - state_full: full state name
        - states: list of states (for multi-state)
        - remote: bool
        - remote_scope: 'usa', 'global', or None
    """
    if not text:
        return _empty_location()
    
    text_lower = text.lower()
    
    result = {
        'raw': text,
        'city': None,
        'state': None,
        'state_full': None,
        'states': [],
        'remote': False,
        'remote_scope': None
    }
    
    # Check for remote
    if 'remote' in text_lower:
        result['remote'] = True
        if 'usa' in text_lower or 'us only' in text_lower or 'united states' in text_lower:
            result['remote_scope'] = 'usa'
        elif 'global' in text_lower or 'worldwide' in text_lower:
            result['remote_scope'] = 'global'
        else:
            result['remote_scope'] = 'usa'  # Default to USA
    
    # Try to find state abbreviation (2 letters)
    state_abbrev_match = re.search(r'\b([A-Z]{2})\b', text)
    if state_abbrev_match:
        abbrev = state_abbrev_match.group(1)
        if abbrev in STATE_NAMES:
            result['state'] = abbrev
            result['state_full'] = STATE_NAMES[abbrev]
            result['states'] = [abbrev]
    
    # Try to find full state name
    if not result['state']:
        for state_name, abbrev in US_STATES.items():
            if state_name in text_lower:
                result['state'] = abbrev
                result['state_full'] = state_name.title()
                result['states'] = [abbrev]
                break
    
    # Try to find city
    for city, state in COMMON_CITIES.items():
        if city in text_lower:
            result['city'] = city.title()
            if not result['state']:
                result['state'] = state
                result['state_full'] = STATE_NAMES.get(state, '')
                result['states'] = [state]
            break
    
    # Try pattern: "City, State" or "City, ST"
    city_state_match = re.search(r'([A-Za-z\s]+),\s*([A-Za-z]{2,})', text)
    if city_state_match and not result['city']:
        potential_city = city_state_match.group(1).strip()
        potential_state = city_state_match.group(2).strip()
        
        # Check if state is valid
        if potential_state.upper() in STATE_NAMES:
            result['city'] = potential_city.title()
            result['state'] = potential_state.upper()
            result['state_full'] = STATE_NAMES[potential_state.upper()]
            result['states'] = [potential_state.upper()]
        elif potential_state.lower() in US_STATES:
            result['city'] = potential_city.title()
            result['state'] = US_STATES[potential_state.lower()]
            result['state_full'] = potential_state.title()
            result['states'] = [US_STATES[potential_state.lower()]]
    
    return result


def normalize_job_location(job: Dict) -> Dict:
    """
    Normalize location for a job dict.
    
    Tries:
    1. Existing location field
    2. Parse from title
    3. Default to Remote USA
    
    Returns updated job dict with location_norm.
    """
    location = job.get('location') or ''
    title = job.get('title') or ''
    
    # Try location field first
    if location:
        loc_norm = extract_location_from_text(location)
        if loc_norm['state'] or loc_norm['remote']:
            job['location_norm'] = loc_norm
            return job
    
    # Try title
    if title:
        loc_norm = extract_location_from_text(title)
        if loc_norm['state'] or loc_norm['remote']:
            # Also set location from title
            if loc_norm['city'] and loc_norm['state_full']:
                job['location'] = f"{loc_norm['city']}, {loc_norm['state_full']}, USA"
            elif loc_norm['state_full']:
                job['location'] = f"{loc_norm['state_full']}, USA"
            elif loc_norm['remote']:
                job['location'] = "Remote, USA"
            
            job['location_norm'] = loc_norm
            return job
    
    # Default: Remote USA (so job is visible)
    job['location'] = job.get('location') or "Remote, USA"
    job['location_norm'] = {
        'raw': job['location'],
        'city': None,
        'state': None,
        'state_full': None,
        'states': [],
        'remote': True,
        'remote_scope': 'usa'
    }
    
    return job


def _empty_location() -> Dict:
    """Return empty location dict."""
    return {
        'raw': '',
        'city': None,
        'state': None,
        'state_full': None,
        'states': [],
        'remote': False,
        'remote_scope': None
    }


# Test
if __name__ == "__main__":
    test_cases = [
        "Program Manager in Raleigh, North Carolina, USA",
        "Senior PM - Remote, USA",
        "Technical Program Manager - San Francisco, CA",
        "Project Manager | New York",
        "TPM (Austin, TX)",
        "Remote Program Manager",
        "",
    ]
    
    for text in test_cases:
        result = extract_location_from_text(text)
        print(f"'{text[:40]}...'")
        print(f"  → City: {result['city']}, State: {result['state']}, Remote: {result['remote']}")
        print()
