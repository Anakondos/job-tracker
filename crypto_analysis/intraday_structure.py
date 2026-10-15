"""
Intraday structure: where in the clock do volatility, persistence and volume live?

Hour-of-day and day-of-week statistics over the full 1H history, split into
two halves (2017-2022 / 2023-2026) so stable structure can be told apart
from noise. The key number per hour is the momentum score

    E[ r(h+1) * sign(r(h)) ]

the expected next-hour return if you simply follow this hour's direction.
Positive = continuation, negative = reversion. Reported with a t-stat.

Usage:
    python -m crypto_analysis.intraday_structure --pair ETH-USD --days 3400
"""

import argparse
import json
import math
import os
from collections import defaultdict

from crypto_analysis.data_fetcher import get_candles

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SPLIT_YEAR = 2023


def tstat(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd else 0.0


def build_rows(c):
    """(ts, ret%, |ret|%, volume, range%, next_ret%)"""
    rows = []
    for i in range(1, len(c)):
        p0 = c[i - 1]["close"]
        r = (c[i]["close"] / p0 - 1) * 100
        rows.append([c[i]["timestamp"], r, abs(r), c[i]["volume"], (c[i]["high"] - c[i]["low"]) / p0 * 100, None])
    for k in range(len(rows) - 1):
        rows[k][5] = rows[k + 1][1]
    return rows


def hour_table(rows):
    H = defaultdict(list)
    for r in rows:
        H[r[0].hour].append(r)
    avg_vol = sum(r[3] for r in rows) / len(rows)
    out = {}
    for h in range(24):
        xs = H[h]
        if not xs:
            continue
        mom = [x[5] * (1 if x[1] > 0 else -1) for x in xs if x[5] is not None and x[1] != 0]
        same = [1 if (x[5] > 0) == (x[1] > 0) else 0 for x in xs if x[5] is not None and x[1] != 0 and x[5] != 0]
        out[h] = dict(
            n=len(xs),
            ret=sum(x[1] for x in xs) / len(xs),
            absret=sum(x[2] for x in xs) / len(xs),
            rng=sum(x[4] for x in xs) / len(xs),
            vol=sum(x[3] for x in xs) / len(xs) / avg_vol,
            mom=sum(mom) / len(mom) if mom else 0.0,
            mom_t=tstat(mom),
            same=sum(same) / len(same) if same else 0.0,
        )
    return out


def dow_table(rows):
    D = defaultdict(list)
    for r in rows:
        D[r[0].weekday()].append(r)
    avg_vol = sum(r[3] for r in rows) / len(rows)
    out = {}
    for d in range(7):
        xs = D[d]
        if not xs:
            continue
        rets = [x[1] for x in xs]
        out[DOW[d]] = dict(n=len(xs), ret=sum(rets) / len(rets), ret_t=tstat(rets),
                           absret=sum(x[2] for x in xs) / len(xs),
                           vol=sum(x[3] for x in xs) / len(xs) / avg_vol)
    return out


def daily_range_persistence(c):
    """Does a quiet day predict a quiet day? Volatility clustering at the daily scale."""
    days = defaultdict(lambda: [float("inf"), 0.0, None])
    for x in c:
        d = days[x["timestamp"].date()]
        d[0] = min(d[0], x["low"]); d[1] = max(d[1], x["high"])
        if d[2] is None:
            d[2] = x["open"]
    seq = [(k, (v[1] - v[0]) / v[2] * 100) for k, v in sorted(days.items()) if v[2]]
    rng = [r for _, r in seq]
    if len(rng) < 30:
        return {}
    srt = sorted(rng)
    q1, med, q3 = srt[len(srt) // 4], srt[len(srt) // 2], srt[3 * len(srt) // 4]
    pairs = list(zip(rng[:-1], rng[1:]))
    mx = sum(a for a, _ in pairs) / len(pairs); my = sum(b for _, b in pairs) / len(pairs)
    cov = sum((a - mx) * (b - my) for a, b in pairs)
    sx = math.sqrt(sum((a - mx) ** 2 for a, _ in pairs)); sy = math.sqrt(sum((b - my) ** 2 for _, b in pairs))
    corr = cov / (sx * sy) if sx and sy else 0.0
    after_quiet = [b for a, b in pairs if a < q1]
    after_wide = [b for a, b in pairs if a > q3]
    return dict(
        days=len(rng), median_range=med, q1=q1, q3=q3, autocorr=corr,
        p_wide_after_quiet=sum(1 for b in after_quiet if b > med) / len(after_quiet) if after_quiet else 0,
        p_wide_after_wide=sum(1 for b in after_wide if b > med) / len(after_wide) if after_wide else 0,
        mean_after_quiet=sum(after_quiet) / len(after_quiet) if after_quiet else 0,
        mean_after_wide=sum(after_wide) / len(after_wide) if after_wide else 0,
    )


def run(pair, days, output):
    c = get_candles(pair, "1h", days)
    rows = build_rows(c)
    h1 = [r for r in rows if r[0].year < SPLIT_YEAR]
    h2 = [r for r in rows if r[0].year >= SPLIT_YEAR]
    full, a, b = hour_table(rows), hour_table(h1), hour_table(h2)

    print(f"\n{'=' * 96}")
    print(f"  INTRADAY STRUCTURE — {pair} 1H  {c[0]['timestamp']:%Y-%m-%d} .. {c[-1]['timestamp']:%Y-%m-%d}  ({len(rows)} hours)")
    print(f"  mom = E[next-hour return x sign(this hour)]  in %   (+ = continuation, - = reversion)")
    print(f"{'=' * 96}")
    print(f"  {'UTC':>4} {'|ret|':>6} {'range':>6} {'vol x':>6} | {'mom all':>8} {'t':>5} {'P same':>6} | {'mom 17-22':>9} {'t':>5} | {'mom 23-26':>9} {'t':>5} | stable")
    print(f"  {'-' * 92}")
    for h in range(24):
        f, x, y = full[h], a[h], b[h]
        stable = "YES" if (x["mom"] > 0) == (y["mom"] > 0) and abs(x["mom_t"]) > 1 and abs(y["mom_t"]) > 1 else ""
        print(f"  {h:>4} {f['absret']:>6.3f} {f['rng']:>6.3f} {f['vol']:>6.2f} | {f['mom']:>+8.4f} {f['mom_t']:>+5.1f} {f['same']:>6.1%} "
              f"| {x['mom']:>+9.4f} {x['mom_t']:>+5.1f} | {y['mom']:>+9.4f} {y['mom_t']:>+5.1f} | {stable}")

    print(f"\n  --- sessions (mean |ret| per hour, volume x, mean hourly return) ---")
    for name, hrs in (("Asia 00-07", range(0, 8)), ("Europe 07-13", range(7, 13)), ("US 13-21", range(13, 21)), ("late US 21-24", range(21, 24))):
        xs = [r for r in rows if r[0].hour in hrs]
        avg_vol = sum(r[3] for r in rows) / len(rows)
        print(f"  {name:<14} |ret| {sum(r[2] for r in xs) / len(xs):.3f}%  vol x{sum(r[3] for r in xs) / len(xs) / avg_vol:.2f}  "
              f"net {sum(r[1] for r in xs) / len(xs):+.4f}%/h")

    print(f"\n  --- day of week ---")
    for name, d in dow_table(rows).items():
        print(f"  {name}  |ret| {d['absret']:.3f}%  vol x{d['vol']:.2f}  mean {d['ret']:+.4f}%/h  t{d['ret_t']:+.1f}")

    dr = daily_range_persistence(c)
    if dr:
        print(f"\n  --- daily range persistence ({dr['days']} days) ---")
        print(f"  median daily range {dr['median_range']:.2f}%   autocorr(range_d, range_d+1) = {dr['autocorr']:+.3f}")
        print(f"  after a quiet day (<Q1 {dr['q1']:.2f}%): next day > median {dr['p_wide_after_quiet']:.0%}, mean range {dr['mean_after_quiet']:.2f}%")
        print(f"  after a wide day  (>Q3 {dr['q3']:.2f}%): next day > median {dr['p_wide_after_wide']:.0%}, mean range {dr['mean_after_wide']:.2f}%")

    os.makedirs(output, exist_ok=True)
    path = os.path.join(output, f"intraday_structure_{pair.replace('-', '_')}.json")
    with open(path, "w") as f:
        json.dump({"pair": pair, "hours": full, "hours_2017_2022": a, "hours_2023_2026": b,
                   "dow": dow_table(rows), "daily_range": dr}, f, indent=2)
    print(f"\n  Saved: {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="ETH-USD")
    p.add_argument("--days", type=int, default=3400)
    p.add_argument("--output", default="crypto_analysis/output")
    a = p.parse_args()
    run(a.pair, a.days, a.output)


if __name__ == "__main__":
    main()
