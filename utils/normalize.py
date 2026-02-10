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
            "is_us": False,
        }

    raw_lower = raw.lower()

    # Check if location contains a non-US country - skip US state matching
    # Use word boundaries to avoid false matches like "Indianapolis" -> "india"
    is_non_us = any(
        re.search(r'\b' + re.escape(country) + r'\b', raw_lower)
        for country in NON_US_COUNTRIES
    )

    # Detect explicit US markers
    has_us_marker = bool(
        re.search(r'\bunited states\b', raw_lower)
        or re.search(r'\busa\b', raw_lower)
        or re.search(r'\bu\.s\.a?\b', raw_lower)
        or re.search(r'\bnorth america\b', raw_lower)
    )

    # Quick return for obvious non-US locations (unless also mentions US)
    if is_non_us and not has_us_marker:
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
            "is_us": False,
        }

    parts = RE_SEPARATORS.split(raw)
    parts = [p.strip() for p in parts if p.strip()]

    states = set()
    cities = []
    detected_remote = False
    remote_scope = None
    is_us = has_us_marker  # Start with explicit US markers

    for part in parts:
        part_lower = part.lower().strip()

        # Skip empty, noise, and country-only parts
        if not part_lower or part_lower in ("united states", "usa", "us", "u.s.", "u.s.a", "north america"):
            is_us = True
            continue

        # Detect remote flags & scope
        # Remote USA patterns
        if re.search(r"\bremote\s*[-\(\)]*\s*(?:usa|u\.?s\.?a?|united\s+states)\b", part_lower) \
           or re.search(r"\b(?:usa?|united\s+states)[-\(\)]*\s*remote\b", part_lower) \
           or re.search(r"\bremote\s*\((?:usa?|united\s+states)\)", part_lower) \
           or re.search(r"\bhome[-\s]*united\s+states\b", part_lower):
            detected_remote = True
            remote_scope = "usa"
            is_us = True
            continue

        # Global remote patterns
        if re.search(r"\bremote\b", part_lower) \
           or re.search(r"\bworldwide\b", part_lower) \
           or re.search(r"\bglobal remote\b", part_lower):
            detected_remote = True
            if not remote_scope:
                remote_scope = "global"
            continue

        # Pattern: "City, STATE_CODE" (e.g. "Raleigh, NC" or "Maine, USA")
        m = RE_CITY_STATE.match(part)
        if m:
            city_part = m.group("city").strip()
            state_part = m.group("state").strip()
            state_lower = state_part.lower()

            # Handle compound: "NC United States" or "Illinois, United States"
            # Strip US markers from state_part
            cleaned_state = re.sub(r'\s*,?\s*(?:united\s+states|usa|u\.?s\.?a?)\s*$', '', state_part, flags=re.IGNORECASE).strip()
            if cleaned_state != state_part:
                is_us = True
                state_part = cleaned_state
                state_lower = state_part.lower()

            # Check if state_part is a US country marker (City, USA / City, United States)
            if state_lower in ("usa", "us", "united states", "u.s.", "u.s.a", ""):
                is_us = True
                # city_part might be a state name: "Maine, USA" -> state=ME
                if city_part.lower() in STATE_MAP:
                    states.add(STATE_MAP[city_part.lower()])
                else:
                    cities.append(city_part)
                continue

            # Check if state_part is a full state name
            if state_lower in STATE_MAP:
                states.add(STATE_MAP[state_lower])
                is_us = True
                if city_part:
                    cities.append(city_part)
                continue

            # Check if state_part is a 2-letter code
            code_upper = state_part.upper()
            if len(code_upper) == 2 and code_upper in STATE_MAP.values():
                states.add(code_upper)
                is_us = True
                if city_part:
                    cities.append(city_part)
                continue

            # Try prefix match for full state names
            found = False
            for full_name, code in STATE_MAP.items():
                if full_name.startswith(state_lower):
                    states.add(code)
                    is_us = True
                    found = True
                    break
            if found:
                if city_part:
                    cities.append(city_part)
                continue

            # Unrecognized comma pattern — treat as city
            cities.append(city_part)
            continue

        # Extract 2-letter state codes in part (standalone)
        codes = RE_STATE_CODE.findall(part)
        for code in codes:
            if code in STATE_MAP.values():
                states.add(code)
                is_us = True

        # Extract full state names mentioned in part
        for full_name, code in STATE_MAP.items():
            if full_name in part_lower:
                states.add(code)
                is_us = True

        # N Locations pattern (e.g. "2 Locations", "5 Locations")
        if re.match(r"^\d+\s+locations?$", part_lower):
            continue

        # Remaining text → city candidate
        if "," not in part and part_lower not in ("remote", "usa", "us", "united states"):
            cities.append(part)

    # If we found states, it's US
    if states:
        is_us = True

    # If remote detected with US context but no explicit scope, set to usa
    if detected_remote and is_us and not remote_scope:
        remote_scope = "usa"
    # If remote + US marker but no states, mark as remote usa
    if detected_remote and is_us and remote_scope == "global":
        remote_scope = "usa"

    # Pick first city if any
    city = cities[0] if cities else None

    # Determine state and full state
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
        "is_us": is_us,
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
