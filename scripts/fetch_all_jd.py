#!/usr/bin/env python3
"""
Batch JD Fetcher — downloads job descriptions from ATS APIs.
NO AI API calls. Uses only free public ATS APIs (Greenhouse, Workday, Lever, etc.)

Usage:
    python3 scripts/fetch_all_jd.py                  # fetch all missing JDs
    python3 scripts/fetch_all_jd.py --limit 500      # fetch up to 500
    python3 scripts/fetch_all_jd.py --newest          # newest first (default)
    python3 scripts/fetch_all_jd.py --score           # also run keyword scorer after fetch
"""

import sys
import os
import time
import json
import argparse
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from parsers.jd_parser import fetch_jd_from_url
from storage.job_storage import _load_jobs

JD_DIR = PROJECT_ROOT / "data" / "jd"
JD_DIR.mkdir(exist_ok=True)


def get_jobs_to_fetch(newest_first: bool = True) -> list:
    """Get pipeline jobs that need JD fetching."""
    jobs = _load_jobs()
    cached = {f.stem for f in JD_DIR.glob("*.txt")}

    eligible = [
        j for j in jobs
        if j.get("id") and j["id"] not in cached
        and (j.get("job_url") or j.get("url"))
        and j.get("role_category") in ("primary", "adjacent")
    ]

    if newest_first:
        eligible.sort(
            key=lambda j: j.get("first_seen") or j.get("added_at") or "",
            reverse=True,
        )

    return eligible


def fetch_single_jd(job: dict) -> dict:
    """Fetch JD for a single job. Returns result dict."""
    job_id = job.get("id", "")
    url = job.get("job_url") or job.get("url", "")
    ats = job.get("ats", "")
    title = job.get("title", "")
    company = job.get("company", "")

    try:
        jd_text = fetch_jd_from_url(url, ats)
        if jd_text and len(jd_text) > 50:
            # Save to file
            jd_file = JD_DIR / f"{job_id}.txt"
            jd_file.write_text(jd_text, encoding="utf-8")
            return {"ok": True, "id": job_id, "chars": len(jd_text)}
        else:
            return {"ok": False, "id": job_id, "error": "empty or too short"}
    except Exception as e:
        return {"ok": False, "id": job_id, "error": str(e)[:80]}


def run_keyword_scorer():
    """Run keyword scorer on all jobs with cached JDs."""
    from utils.job_scorer import score_jobs_batch
    from storage.job_storage import _load_jobs, _save_jobs

    print("\n📊 Running keyword scorer...")
    jobs = _load_jobs()
    unscored = [j for j in jobs if not j.get("kw_score")]
    if not unscored:
        print("All jobs already scored.")
        return

    score_jobs_batch(unscored)
    scored = sum(1 for j in unscored if j.get("kw_score"))

    # Save back
    job_map = {j["id"]: j for j in unscored if j.get("id")}
    for j in jobs:
        if j.get("id") in job_map:
            j.update(job_map[j["id"]])
    _save_jobs(jobs)

    # Stats
    from collections import Counter
    recs = Counter(j.get("kw_recommendation", "?") for j in unscored if j.get("kw_score"))
    print(f"✅ Scored {scored} jobs: {recs.get('APPLY', 0)} APPLY, "
          f"{recs.get('CONSIDER', 0)} CONSIDER, {recs.get('SKIP', 0)} SKIP")


def main():
    parser = argparse.ArgumentParser(description="Batch fetch JDs from ATS APIs (free)")
    parser.add_argument("--limit", type=int, default=0, help="Max jobs to fetch (0=all)")
    parser.add_argument("--newest", action="store_true", default=True, help="Newest first")
    parser.add_argument("--oldest", action="store_true", help="Oldest first")
    parser.add_argument("--score", action="store_true", help="Run keyword scorer after fetch")
    parser.add_argument("--ats", type=str, default="", help="Filter by ATS type")
    args = parser.parse_args()

    newest = not args.oldest

    print("=" * 60)
    print("🔍 JD Batch Fetcher (FREE — no AI API)")
    print("=" * 60)

    # Get jobs to fetch
    to_fetch = get_jobs_to_fetch(newest_first=newest)
    if args.ats:
        to_fetch = [j for j in to_fetch if j.get("ats") == args.ats]
    if args.limit:
        to_fetch = to_fetch[:args.limit]

    cached_count = len(list(JD_DIR.glob("*.txt")))
    print(f"📁 Already cached: {cached_count} JDs")
    print(f"📋 To fetch: {len(to_fetch)} jobs")

    if not to_fetch:
        print("Nothing to fetch!")
        if args.score:
            run_keyword_scorer()
        return

    # ATS breakdown
    from collections import Counter
    ats_counts = Counter(j.get("ats", "?") for j in to_fetch)
    print(f"\nATS breakdown:")
    for ats, cnt in ats_counts.most_common():
        print(f"  {ats:20s}: {cnt}")

    print(f"\n🚀 Starting fetch (newest first)...\n")

    success = 0
    errors = 0
    start_time = time.time()

    for i, job in enumerate(to_fetch):
        company = job.get("company", "?")
        title = job.get("title", "?")
        ats = job.get("ats", "?")

        result = fetch_single_jd(job)

        if result["ok"]:
            success += 1
            print(f"  ✅ [{i+1}/{len(to_fetch)}] {company} | {title[:45]} | {result['chars']} chars")
        else:
            errors += 1
            print(f"  ❌ [{i+1}/{len(to_fetch)}] {company} | {title[:45]} | {result['error'][:40]}")

        # Rate limit: 0.3s between requests
        time.sleep(0.3)

        # Progress every 50
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            remaining = (len(to_fetch) - i - 1) / rate if rate > 0 else 0
            print(f"\n  📊 Progress: {i+1}/{len(to_fetch)} | "
                  f"✅ {success} ok, ❌ {errors} err | "
                  f"~{remaining/60:.0f} min left\n")

    elapsed = time.time() - start_time
    print(f"\n{'=' * 60}")
    print(f"✅ Done in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"   Success: {success}")
    print(f"   Errors:  {errors}")
    print(f"   Total cached: {len(list(JD_DIR.glob('*.txt')))}")
    print(f"{'=' * 60}")

    # Run keyword scorer
    if args.score:
        run_keyword_scorer()


if __name__ == "__main__":
    main()
