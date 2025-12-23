"""
Job cache manager with TTL
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
TTL_HOURS = 6

def get_cache_path(cache_key: str) -> Path:
    return CACHE_DIR / f"jobs_{cache_key}.json"

def is_cache_valid(cache_data: Dict) -> bool:
    if not cache_data:
        return False
    
    last_updated_str = cache_data.get("last_updated")
    if not last_updated_str:
        return False
    
    try:
        last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = now - last_updated
        return age < timedelta(hours=TTL_HOURS)
    except:
        return False

def load_cache(cache_key: str = "all") -> Optional[Dict]:
    cache_path = get_cache_path(cache_key)
    
    if not cache_path.exists():
        return None
    
    try:
        with cache_path.open("r", encoding="utf-8") as f:
            cache_data = json.load(f)
        
        if is_cache_valid(cache_data):
            return cache_data
        else:
            print(f"Cache expired for '{cache_key}'")
            return None
    except:
        return None

def save_cache(cache_key: str, jobs: List[Dict]) -> bool:
    cache_data = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "ttl_hours": TTL_HOURS,
        "cache_key": cache_key,
        "jobs_count": len(jobs),
        "jobs": jobs
    }
    
    cache_path = get_cache_path(cache_key)
    
    try:
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"✅ Cached {len(jobs)} jobs for '{cache_key}'")
        return True
    except Exception as e:
        print(f"❌ Cache save error: {e}")
        return False

def clear_cache(cache_key: str = None) -> bool:
    try:
        if cache_key:
            cache_path = get_cache_path(cache_key)
            if cache_path.exists():
                cache_path.unlink()
        else:
            for cache_file in CACHE_DIR.glob("jobs_*.json"):
                cache_file.unlink()
        return True
    except:
        return False

def get_cache_info(cache_key: str = "all") -> Dict:
    cache_data = load_cache(cache_key)
    
    if cache_data:
        last_updated = cache_data.get("last_updated")
        
        try:
            last_updated_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - last_updated_dt
            age_minutes = int(age.total_seconds() / 60)
            
            if age_minutes < 60:
                age_str = f"{age_minutes} minutes ago"
            else:
                age_hours = age_minutes // 60
                age_str = f"{age_hours} hours ago"
        except:
            age_str = "unknown"
        
        return {
            "exists": True,
            "valid": True,
            "last_updated": last_updated,
            "age": age_str,
            "jobs_count": cache_data.get("jobs_count", 0),
            "ttl_hours": TTL_HOURS
        }
    else:
        cache_path = get_cache_path(cache_key)
        return {
            "exists": cache_path.exists(),
            "valid": False,
            "last_updated": None,
            "age": None,
            "jobs_count": 0,
            "ttl_hours": TTL_HOURS
        }
