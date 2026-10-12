"""
Retracement depth statistics: how deep do corrections go after an impulse?

Uses a ZigZag swing detector to find impulse -> correction pairs and
measures correction depth as % of the preceding impulse.

Usage:
    python -m crypto_analysis.retracement_stats --pair ETH-USD --granularity 6h --days 365
    python -m crypto_analysis.retracement_stats --pair BTC-USD --granularity 1d --days 730 --zigzag 5
"""

import argparse
import json
import os
from datetime import datetime

from crypto_analysis.data_fetcher import get_candles

FIB_BUCKETS = [(0, 25), (25, 38.2), (38.2, 50), (50, 61.8), (61.8, 78.6), (78.6, 100), (100, 1e9)]


def zigzag(highs: list[float], lows: list[float], threshold_pct: float) -> list[tuple[int, float, str]]:
    """Return swing points as (index, price, 'H'|'L'). A swing is confirmed
    once price moves threshold_pct% against the current extreme."""
    pivots = []
    trend = None
    ext_idx, ext_price = 0, highs[0]

    for i in range(1, len(highs)):
        if trend is None:
            if highs[i] > ext_price:
                ext_idx, ext_price, trend = i, highs[i], "up"
            elif lows[i] < ext_price:
                ext_idx, ext_price, trend = i, lows[i], "down"
            continue

        if trend == "up":
            if highs[i] >= ext_price:
                ext_idx, ext_price = i, highs[i]
            if (ext_price - lows[i]) / ext_price * 100 >= threshold_pct:
                pivots.append((ext_idx, ext_price, "H"))
                trend, ext_idx, ext_price = "down", i, lows[i]
        else:
            if lows[i] <= ext_price:
                ext_idx, ext_price = i, lows[i]
            if (highs[i] - ext_price) / ext_price * 100 >= threshold_pct:
                pivots.append((ext_idx, ext_price, "L"))
                trend, ext_idx, ext_price = "up", i, highs[i]
    return pivots


def measure_retraces(candles: list[dict], pivots: list[tuple]) -> list[dict]:
    """For each pivot triple (p1 -> p2 impulse, p2 -> p3 correction) compute retrace %."""
    out = []
    for k in range(len(pivots) - 2):
        i1, p1, t1 = pivots[k]
        i2, p2, _ = pivots[k + 1]
        i3, p3, _ = pivots[k + 2]
        impulse = abs(p2 - p1)
        if impulse == 0:
            continue
        out.append({
            "peak_time": candles[i2]["timestamp"].strftime("%Y-%m-%d %H:%M"),
            "direction": "UP" if t1 == "L" else "DOWN",
            "impulse_pct": round(impulse / p1 * 100, 2),
            "retrace_pct": round(abs(p3 - p2) / impulse * 100, 1),
            "bars_impulse": i2 - i1,
            "bars_correction": i3 - i2,
        })
    return out


def percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def summarize(retraces: list[dict], min_impulse_pct: float) -> dict:
    """Summary stats, restricted to impulses >= min_impulse_pct (filters ZigZag noise)."""
    vals = sorted(r["retrace_pct"] for r in retraces if r["impulse_pct"] >= min_impulse_pct)
    n = len(vals)
    if n == 0:
        return {"n": 0}
    buckets = {}
    for lo, hi in FIB_BUCKETS:
        label = f"{lo:g}-{hi:g}%" if hi < 1e9 else ">100%"
        buckets[label] = sum(1 for v in vals if lo <= v < hi)
    return {
        "n": n,
        "min_impulse_pct": min_impulse_pct,
        "min": vals[0],
        "p25": percentile(vals, 0.25),
        "median": percentile(vals, 0.5),
        "p75": percentile(vals, 0.75),
        "max": vals[-1],
        "mean": round(sum(vals) / n, 1),
        "share_ge_25": round(sum(1 for v in vals if v >= 25) / n, 3),
        "share_ge_38": round(sum(1 for v in vals if v >= 38.2) / n, 3),
        "share_ge_50": round(sum(1 for v in vals if v >= 50) / n, 3),
        "share_ge_62": round(sum(1 for v in vals if v >= 61.8) / n, 3),
        "share_ge_100": round(sum(1 for v in vals if v >= 100) / n, 3),
        "buckets": buckets,
    }


def print_summary(title: str, s: dict):
    print(f"\n  --- {title} (n={s['n']}) ---")
    if s["n"] == 0:
        print("  no data")
        return
    print(f"  min {s['min']:.0f}%  p25 {s['p25']:.0f}%  median {s['median']:.0f}%  p75 {s['p75']:.0f}%  max {s['max']:.0f}%")
    print(f"  >=25%: {s['share_ge_25']:.0%}   >=38%: {s['share_ge_38']:.0%}   >=50%: {s['share_ge_50']:.0%}   "
          f">=62%: {s['share_ge_62']:.0%}   fully erased: {s['share_ge_100']:.0%}")
    for label, c in s["buckets"].items():
        bar = "#" * int(c / s["n"] * 40)
        print(f"  {label:>10} {c:>4} {c / s['n']:>5.0%}  {bar}")


def run(pair: str, granularity: str, days: int, zigzag_pct: float, output_dir: str):
    print(f"Fetching {pair} {granularity} candles, {days} days...")
    candles = get_candles(pair, granularity, days)
    print(f"  -> {len(candles)} candles, {candles[0]['timestamp']:%Y-%m-%d} .. {candles[-1]['timestamp']:%Y-%m-%d}")

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    pivots = zigzag(highs, lows, zigzag_pct)
    retraces = measure_retraces(candles, pivots)
    print(f"  ZigZag {zigzag_pct}%: {len(pivots)} swings, {len(retraces)} impulse/correction pairs")

    # Noise filter: an impulse must be >= 2x the ZigZag threshold to be
    # distinguishable from the correction that confirmed it.
    strong = 2 * zigzag_pct
    big = 4 * zigzag_pct

    all_s = summarize(retraces, 0)
    strong_s = summarize(retraces, strong)
    big_s = summarize(retraces, big)

    print(f"\n{'=' * 70}")
    print(f"  RETRACEMENT DEPTH — {pair} {granularity}, {days} days, ZigZag {zigzag_pct}%")
    print(f"{'=' * 70}")
    print_summary("ALL impulses", all_s)
    print_summary(f"Impulses >= {strong:g}% (real moves)", strong_s)
    print_summary(f"Impulses >= {big:g}% (strong moves)", big_s)

    # Largest impulses individually
    top = sorted(retraces, key=lambda r: r["impulse_pct"], reverse=True)[:12]
    print(f"\n  --- 12 largest impulses ---")
    print(f"  {'peak':>16} {'dir':>5} {'impulse':>8} {'retrace':>8} {'bars I/C':>9}")
    for r in top:
        print(f"  {r['peak_time']:>16} {r['direction']:>5} {r['impulse_pct']:>7.1f}% {r['retrace_pct']:>7.0f}% "
              f"{r['bars_impulse']:>4}/{r['bars_correction']:<4}")

    os.makedirs(output_dir, exist_ok=True)
    tag = f"{pair.replace('-', '_')}_{granularity}_{days}d"
    path = os.path.join(output_dir, f"retracement_{tag}.json")
    with open(path, "w") as f:
        json.dump({
            "pair": pair, "granularity": granularity, "days": days, "zigzag_pct": zigzag_pct,
            "generated": datetime.utcnow().isoformat(),
            "all": all_s, "strong": strong_s, "big": big_s, "retraces": retraces,
        }, f, indent=2)
    print(f"\n  Saved: {path}")


def main():
    p = argparse.ArgumentParser(description="Impulse/correction retracement statistics")
    p.add_argument("--pair", default="ETH-USD")
    p.add_argument("--granularity", default="6h", choices=["1m", "5m", "15m", "1h", "6h", "1d"])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--zigzag", type=float, default=None, help="swing threshold %% (default: 3 for 6h, 5 for 1d, 2 for 1h)")
    p.add_argument("--output", default="crypto_analysis/output")
    a = p.parse_args()
    zz = a.zigzag if a.zigzag is not None else {"1d": 5.0, "6h": 3.0, "1h": 2.0}.get(a.granularity, 2.0)
    run(a.pair, a.granularity, a.days, zz, a.output)


if __name__ == "__main__":
    main()
