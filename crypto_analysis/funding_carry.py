"""
Cash-and-carry on Coinbase INTX: long spot, short perp, collect hourly funding.
Delta-neutral, so each hour's P&L is just the funding rate (received when
positive, paid when negative). Pulls the full funding history and reports
what share of hours / days / months were actually positive.

Returns are on perp notional; on total deployed capital (spot + margin)
divide by roughly 1.25-1.5. Basis P&L at entry/exit and exchange risk are
not modeled.

Usage:
    python -m crypto_analysis.funding_carry
    python -m crypto_analysis.funding_carry --symbols ETH-PERP SOL-PERP
"""

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime

import requests

BASE = "https://api.international.coinbase.com/api/v1"


def fetch_funding(symbol: str) -> list[tuple[datetime, float]]:
    out, off = [], 0
    while True:
        r = requests.get(f"{BASE}/instruments/{symbol}/funding",
                         params={"result_limit": 100, "result_offset": off}, timeout=20)
        r.raise_for_status()
        res = r.json().get("results", [])
        if not res:
            break
        out.extend(res)
        off += len(res)
        if len(res) < 100 or off > 100000:
            break
        time.sleep(0.08)
    rows = [(datetime.fromisoformat(x["event_time"].replace("Z", "+00:00")).replace(tzinfo=None),
             float(x["funding_rate"])) for x in out]
    rows.sort()
    return rows


def analyze(symbol: str, rows: list[tuple[datetime, float]]) -> dict:
    rates = [r for _, r in rows]
    hours = len(rates)
    by_day, by_month, by_year = defaultdict(float), defaultdict(float), defaultdict(float)
    for t, r in rows:
        by_day[t.date()] += r
        by_month[(t.year, t.month)] += r
        by_year[t.year] += r
    cum, s, peak, dd = [], 0.0, 0.0, 0.0
    for r in rates:
        s += r
        peak = max(peak, s)
        dd = max(dd, peak - s)
    streak = longest = 0
    for d in sorted(by_day):
        streak = streak + 1 if by_day[d] <= 0 else 0
        longest = max(longest, streak)
    months = sorted(by_month.items())
    return {
        "symbol": symbol, "hours": hours,
        "from": rows[0][0].strftime("%Y-%m-%d"), "to": rows[-1][0].strftime("%Y-%m-%d"),
        "hours_positive": sum(1 for r in rates if r > 0) / hours,
        "days_positive": sum(1 for v in by_day.values() if v > 0) / len(by_day),
        "months_positive": sum(1 for v in by_month.values() if v > 0) / len(by_month),
        "total_pct": sum(rates) * 100,
        "annualized_pct": sum(rates) / hours * 8760 * 100,
        "max_dd_pct": dd * 100,
        "longest_nonpositive_days": longest,
        "by_year_pct": {str(y): v * 100 for y, v in sorted(by_year.items())},
        "worst_months": [(f"{k[0]}-{k[1]:02d}", v * 100) for k, v in sorted(months, key=lambda kv: kv[1])[:4]],
        "best_months": [(f"{k[0]}-{k[1]:02d}", v * 100) for k, v in sorted(months, key=lambda kv: -kv[1])[:4]],
    }


def report(s: dict):
    print(f"\n{s['symbol']}: {s['hours']} hourly rates, {s['from']} .. {s['to']}")
    print(f"  hours positive {s['hours_positive']:.1%}   days positive {s['days_positive']:.1%}   months positive {s['months_positive']:.1%}")
    print(f"  total funding {s['total_pct']:+.2f}% of notional   annualized {s['annualized_pct']:+.2f}%")
    print(f"  max drawdown of cumulative funding {s['max_dd_pct']:.2f}%   longest run of non-positive days {s['longest_nonpositive_days']}")
    print("  by year:", "  ".join(f"{y}:{v:+.2f}%" for y, v in s["by_year_pct"].items()))
    print("  worst months:", ", ".join(f"{m} {v:+.2f}%" for m, v in s["worst_months"]))
    print("  best months: ", ", ".join(f"{m} {v:+.2f}%" for m, v in s["best_months"]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["ETH-PERP", "BTC-PERP"])
    p.add_argument("--output", default="crypto_analysis/output")
    a = p.parse_args()
    os.makedirs(a.output, exist_ok=True)
    for sym in a.symbols:
        rows = fetch_funding(sym)
        if not rows:
            print(f"\n{sym}: no data")
            continue
        s = analyze(sym, rows)
        report(s)
        with open(os.path.join(a.output, f"funding_carry_{sym}.json"), "w") as f:
            json.dump(s, f, indent=2)


if __name__ == "__main__":
    main()
