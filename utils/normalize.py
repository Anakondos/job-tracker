import re
from typing import Optional

STATE_MAP = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

RE_STATE_CODE = re.compile(r"\b([A-Z]{2})\b")
RE_CITY_STATE = re.compile(r"(?P<city>[a-zA-Z\s]+),\s*(?P<state>[A-Za-z\s]{2,})")
RE_SEPARATORS = re.compile(r"[;|\n\/]+")


# Countries to exclude from US state matching
NON_US_COUNTRIES = {
    "india", "canada", "uk", "united kingdom", "germany", "france", "spain",
    "italy", "japan", "china", "australia", "brazil", "mexico", "ireland",
    "netherlands", "sweden", "norway", "denmark", "finland", "poland",
    "singapore", "hong kong", "israel", "philippines", "vietnam", "thailand",
    "indonesia", "malaysia", "south korea", "taiwan", "argentina", "chile",
    "colombia", "peru", "south africa", "nigeria", "egypt", "uae",
    "united arab emirates", "saudi arabia", "portugal", "belgium", "austria",
    "switzerland", "czech republic", "romania", "hungary", "ukraine", "russia",
    "new zealand", "costa rica", "puerto rico"
}


def normalize_location(location: Optional[str]) -> dict:
    raw = location or ""
    if not raw:
        return {
            "raw": "",
            "city": None,
            "state": None,
            "state_full": None,
            "states": [],
            "remote": False,
            "remote_scope": None,
        }

    # Check if location contains a non-US country - skip US state matching
    raw_lower = raw.lower()
    # Use word boundaries to avoid false matches like "Indianapolis" -> "india"
    is_non_us = any(
        re.search(r'\b' + re.escape(country) + r'\b', raw_lower) 
        for country in NON_US_COUNTRIES
    )
    
    # Quick return for obvious non-US locations
    if is_non_us and "united states" not in raw_lower and "usa" not in raw_lower:
        # Extract city (first part before comma)
        city = None
        if "," in raw:
            city = raw.split(",")[0].strip()
        return {
            "raw": raw,
            "city": city,
            "state": None,
            "state_full": None,
            "states": [],
            "remote": "remote" in raw_lower,
            "remote_scope": "global" if "remote" in raw_lower else None,
        }

    parts = RE_SEPARATORS.split(raw)
    parts = [p.strip() for p in parts if p.strip()]

    states = set()
    cities = []
    detected_remote = False
    remote_scope = None

    for part in parts:
        part_lower = part.lower()

        # Detect remote flags & scope
        # Remote USA patterns
        if re.search(r"\bremote\s*[-\(\)]*\s*usa\b", part_lower) \
           or re.search(r"\bus[-\(\)]*\s*remote\b", part_lower) \
           or re.search(r"\bunited states[, ]*remote\b", part_lower) \
           or re.search(r"\bremote\s*\(usa\)", part_lower):
            detected_remote = True
            remote_scope = "usa"
            continue

        # Global remote patterns
        if re.search(r"\bremote\b", part_lower) \
           or re.search(r"\bworldwide\b", part_lower) \
           or re.search(r"\bglobal remote\b", part_lower):
            detected_remote = True
            if not remote_scope:
                remote_scope = "global"
            continue

        # Try city,state pattern
        m = RE_CITY_STATE.match(part)
        if m:
            city = m.group("city").strip()
            state_name = m.group("state").strip().lower()
            city = city if city else None
            cities.append(city)

            if state_name in STATE_MAP:
                states.add(STATE_MAP[state_name])
            elif re.fullmatch(r"[A-Z]{2}", state_name.upper()):
                states.add(state_name.upper())
            else:
                # try to map full state names
                found = False
                for full_name, code in STATE_MAP.items():
                    if full_name.startswith(state_name):
                        states.add(code)
                        found = True
                        break
                if not found and len(state_name) == 2:
                    states.add(state_name.upper())
            continue

        # Extract 2-letter state codes in part
        codes = RE_STATE_CODE.findall(part)
        for code in codes:
            if code in STATE_MAP.values():
                states.add(code)

        # Extract full state names and convert
        for full_name, code in STATE_MAP.items():
            if full_name in part_lower:
                states.add(code)

        # Extract city if part looks like a city (poor heuristic)
        if "," not in part and part_lower not in ["remote", "usa", "us", "united states"]:
            cities.append(part)

    # Pick first city if any
    city = cities[0] if cities else None

    # Determine state and full state
    # For multi-location jobs, use first state
    state = None
    state_full = None
    if states:
        state = sorted(list(states))[0]  # First state alphabetically
        for full_name, code in STATE_MAP.items():
            if code == state:
                state_full = full_name.title()
                break

    return {
        "raw": raw,
        "city": city,
        "state": state,
        "state_full": state_full,
        "states": sorted(list(states)),
        "remote": detected_remote,
        "remote_scope": remote_scope,
    }


def classify_role(title: Optional[str], description: Optional[str] = None) -> dict:
    if not title:
        return {"role_family": "other", "confidence": 0.0, "reason": "No title provided"}

    title_lower = title.lower()
    description_lower = description.lower() if description else ""

    # Negative keywords to exclude certain roles
    negatives = [
        "engineer",
        "developer",
        "sales",
        "account executive",
        "security",
        "incident response",
    ]

    if any(neg in title_lower for neg in negatives):
        return {
            "role_family": "other",
            "confidence": 0.9,
            "reason": "Negative keyword detected in title",
        }

    # Define keyword sets for role families
    product_keywords = [
        "product manager",
        "product owner",
        "group product",
        "principal product",
        "apm",
    ]
    tpm_keywords = [
        "technical program manager",
        "program manager",
        "delivery manager",
        "release manager",
        "implementation",
    ]
    project_keywords = ["project manager", "pmo", "project coordinator"]

    # check for product keywords
    for keyword in product_keywords:
        if keyword in title_lower:
            return {"role_family": "product", "confidence": 1.0, "reason": f"Matched keyword: {keyword}"}
    # check for tpm/program keywords
    for keyword in tpm_keywords:
        if keyword in title_lower:
            return {"role_family": "tpm_program", "confidence": 1.0, "reason": f"Matched keyword: {keyword}"}
    # check for project keywords
    for keyword in project_keywords:
        if keyword in title_lower:
            return {"role_family": "project", "confidence": 1.0, "reason": f"Matched keyword: {keyword}"}

    # Strategic Project Lead case
    if "strategic project lead" in title_lower:
        # default to tpm_program with medium confidence, unless negatives exist
        if any(neg in title_lower for neg in negatives):
            return {"role_family": "other", "confidence": 0.8, "reason": "Negative keyword for Strategic Project Lead"}
        return {
            "role_family": "tpm_program",
            "confidence": 0.7,
            "reason": "Defaulted Strategic Project Lead to tpm_program",
        }

    return {"role_family": "other", "confidence": 0.5, "reason": "No matching keywords found"}
