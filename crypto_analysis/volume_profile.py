"""
Volume profile day trade: does price return to the prior day's point of control,
and can the value-area edges be traded?

Profile per UTC day from 1H candles: each candle's volume is spread evenly over
its high-low range into bins of 0.1% of the day's open. POC = highest-volume
bin; value area = contiguous bins around the POC holding 70% of the day's volume.

Measured
  P(price touches the prior day's POC during the next day), by how far the
  day opened from that POC.

Strategies (select 2017-2022 / evaluate 2023-2026, 1% risk, 0.1% fees)
  A  value-area fade: first 1H close of the day outside the prior value area ->
     enter toward the POC, stop 1xATR beyond entry, target POC, flat at day end
  B  "80% rule": day opens outside the prior value area; on the first 1H close
     back inside, enter toward the far edge, stop beyond the day's extreme so
     far, target the far edge, flat at day end
  Each with and without a trend filter (SMA1200 on 1H).

Usage:
    python -m crypto_analysis.volume_profile --pair ETH-USD --days 3400
"""

import argparse
import json
import math
import os
from collections import defaultdict

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import atr, sma

TRAIN, TEST = (2017, 2022), (2023, 2026)


def tstat(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd else 0.0


def day_profiles(c):
    days = defaultdict(list)
    for i, x in enumerate(c):
        days[x["timestamp"].date()].append(i)
    prof = {}
    for d, idxs in days.items():
        if len(idxs) < 20:
            continue
        w = c[idxs[0]]["open"] * 0.001
        bins = defaultdict(float)
        for i in idxs:
            b0, b1 = int(c[i]["low"] // w), int(c[i]["high"] // w)
            share = c[i]["volume"] / (b1 - b0 + 1)
            for b in range(b0, b1 + 1):
                bins[b] += share
        total = sum(bins.values())
        if total <= 0:
            continue
        poc = max(bins, key=bins.get)
        lo_b = hi_b = poc
        acc = bins[poc]
        while acc < 0.7 * total and hi_b - lo_b < 5000:
            up, dn = bins.get(hi_b + 1, 0.0), bins.get(lo_b - 1, 0.0)
            if up >= dn:
                hi_b += 1; acc += up
            else:
                lo_b -= 1; acc += dn
        prof[d] = dict(poc=(poc + 0.5) * w, vah=(hi_b + 1) * w, val=lo_b * w, idxs=idxs)
    return prof


def touch_stats(c, prof, order):
    buckets = defaultdict(lambda: [0, 0])
    for k in range(1, len(order)):
        p, today = prof.get(order[k - 1]), prof.get(order[k])
        if not p or not today:
            continue
        idxs = today["idxs"]
        dist = abs(c[idxs[0]]["open"] - p["poc"]) / p["poc"] * 100
        key = "<0.5%" if dist < 0.5 else "0.5-1%" if dist < 1 else "1-2%" if dist < 2 else "2-4%" if dist < 4 else ">4%"
        touched = any(c[i]["low"] <= p["poc"] <= c[i]["high"] for i in idxs)
        buckets[key][0] += touched; buckets[key][1] += 1
        buckets["all"][0] += touched; buckets["all"][1] += 1
    return buckets


def trade_day(c, a14, trend, p, idxs, strat, use_filter, fee, risk_pct, equity):
    """Returns (pnl, R, reason) or None."""
    j0 = idxs[0]
    px0 = c[j0]["close"]
    side = entry = stop = tgt = None
    start = j0 + 1
    if strat == "A":
        if px0 > p["vah"]:
            side, entry, stop, tgt = -1, px0, px0 + a14[j0], p["poc"]
        elif px0 < p["val"]:
            side, entry, stop, tgt = 1, px0, px0 - a14[j0], p["poc"]
        if side is None:
            return None
    else:  # B: 80% rule
        o = c[j0]["open"]
        came_from = "above" if o > p["vah"] else "below" if o < p["val"] else None
        if came_from is None:
            return None
        ext = c[j0]["high"] if came_from == "above" else c[j0]["low"]
        for j in idxs[1:]:
            px = c[j]["close"]
            ext = max(ext, c[j]["high"]) if came_from == "above" else min(ext, c[j]["low"])
            if p["val"] <= px <= p["vah"]:
                if came_from == "above":
                    side, entry, stop, tgt = -1, px, ext, p["val"]
                else:
                    side, entry, stop, tgt = 1, px, ext, p["vah"]
                start = j + 1
                break
        if side is None:
            return None
    if use_filter and trend[j0] is not None:
        if (side == 1 and c[j0]["close"] < trend[j0]) or (side == -1 and c[j0]["close"] > trend[j0]):
            return None
    if trend[j0] is None or a14[j0] is None or abs(entry - stop) <= 0:
        return None
    risk = equity * risk_pct / 100
    qty = min(risk / abs(entry - stop), 10.0 * equity / entry)  # 10x leverage cap
    risk = qty * abs(entry - stop)
    ex = reason = None
    for j in range(start, idxs[-1] + 1):
        b = c[j]
        if (side == 1 and b["low"] <= stop) or (side == -1 and b["high"] >= stop):
            ex = (min(b["open"], stop) if side == 1 else max(b["open"], stop)); reason = "stop"; break
        if (side == 1 and b["high"] >= tgt) or (side == -1 and b["low"] <= tgt):
            ex = (max(b["open"], tgt) if side == 1 else min(b["open"], tgt)); reason = "target"; break
    if ex is None:
        ex, reason = c[idxs[-1]]["close"], "time"
    pnl = qty * (ex - entry) * side - qty * (entry + ex) * fee / 200
    return pnl, pnl / risk, reason


def backtest(c, a14, trend, prof, order, strat, use_filter, years, fee=0.10, risk_pct=1.0):
    equity, peak, dd, trades = 100.0, 100.0, 0.0, []
    for k in range(1, len(order)):
        d = order[k]
        if not (years[0] <= d.year <= years[1]):
            continue
        p, today = prof.get(order[k - 1]), prof.get(d)
        if not p or not today:
            continue
        r = trade_day(c, a14, trend, p, today["idxs"], strat, use_filter, fee, risk_pct, equity)
        if r is None:
            continue
        pnl, R, reason = r
        equity += pnl; peak = max(peak, equity); dd = max(dd, (peak - equity) / peak * 100)
        trades.append(dict(pnl=pnl, R=R, reason=reason, year=d.year))
    return trades, equity, dd


def stats(trades, equity, dd):
    if not trades:
        return "n   0"
    w = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    l = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    wr = sum(1 for t in trades if t["pnl"] > 0) / len(trades)
    ex = defaultdict(int)
    for t in trades:
        ex[t["reason"]] += 1
    return (f"n{len(trades):>4} wr{wr:>4.0%} {equity - 100:>+7.1f}% pf{(w / l if l else 99):>4.2f} "
            f"R{sum(t['R'] for t in trades) / len(trades):>+5.2f} t{tstat([t['R'] for t in trades]):>+4.1f} dd{dd:>4.1f}  "
            f"tgt:{ex['target']} stop:{ex['stop']} time:{ex['time']}")


def run(pair, days, output):
    c = get_candles(pair, "1h", days)
    closes = [x["close"] for x in c]
    a14 = atr([x["high"] for x in c], [x["low"] for x in c], closes, 14)
    trend = sma(closes, 1200)
    prof = day_profiles(c)
    order = sorted(prof)
    print(f"\n{'=' * 100}")
    print(f"  VOLUME PROFILE — {pair} 1H  {c[0]['timestamp']:%Y-%m-%d} .. {c[-1]['timestamp']:%Y-%m-%d}  ({len(prof)} daily profiles)")
    print(f"{'=' * 100}")

    print(f"\n  --- P(touch prior-day POC during the day), by distance of the open from POC ---")
    b = touch_stats(c, prof, order)
    for key in ("<0.5%", "0.5-1%", "1-2%", "2-4%", ">4%", "all"):
        t, n = b[key]
        if n:
            print(f"  open {key:>6} from POC: touched {t / n:>5.1%}  (n={n})")

    for strat, name in (("A", "A  value-area fade to POC"), ("B", "B  80% rule, VA re-entry to far edge")):
        print(f"\n  --- {name} ---")
        for use_filter in (False, True):
            tag = "with trend filter" if use_filter else "no filter       "
            tr_a, eq_a, dd_a = backtest(c, a14, trend, prof, order, strat, use_filter, TRAIN)
            tr_b, eq_b, dd_b = backtest(c, a14, trend, prof, order, strat, use_filter, TEST)
            print(f"  {tag}  TRAIN {stats(tr_a, eq_a, dd_a)}")
            print(f"  {' ' * len(tag)}  TEST  {stats(tr_b, eq_b, dd_b)}")

    os.makedirs(output, exist_ok=True)
    path = os.path.join(output, f"volume_profile_{pair.replace('-', '_')}.json")
    with open(path, "w") as f:
        json.dump({"pair": pair, "touch": {k: {"touched": v[0], "n": v[1]} for k, v in b.items()}}, f, indent=2)
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
