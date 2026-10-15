"""
Opening-range breakout day trade, swept over every hour of the day as the
session open. Hours are SELECTED on 2017-2022 and EVALUATED on 2023-2026,
so the test column is out-of-sample for the choice of hour.

Rules for open hour H (UTC):
  range    high/low of the 1H candle starting at H
  filter   long only if close(H) > SMA(1200) on 1H (~SMA200 on 6H); short only if below
  entry    stop order at range high (long) / range low (short), valid for `window` bars
  stop     opposite side of the range
  target   entry +/- target_r x range
  time out close of bar H + hold
  one trade per day, risk_pct of equity per trade, fee_rt_pct per round trip

Usage:
    python -m crypto_analysis.session_orb --pair ETH-USD --days 3400
"""

import argparse
import json
import math
import os

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import sma, atr

TRAIN = (2017, 2022)
TEST = (2023, 2026)


def tstat(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / (sd / math.sqrt(n)) if sd else 0.0


def backtest(c, trend, a14, by_dayhour, days, H, *, window, hold, target_r, use_filter, fee, risk_pct, years,
             max_lev=10.0, min_range_atr=0.0):
    equity, peak, dd, trades = 100.0, 100.0, 0.0, []
    for d in days:
        if not (years[0] <= d.year <= years[1]):
            continue
        i = by_dayhour.get((d, H))
        if i is None or trend[i] is None or a14[i] is None or i + hold >= len(c):
            continue
        if (c[i + hold]["timestamp"] - c[i]["timestamp"]).total_seconds() != hold * 3600:
            continue  # data gap inside the trade window
        rh, rl = c[i]["high"], c[i]["low"]
        rng = rh - rl
        if rng <= 0 or rng < min_range_atr * a14[i]:
            continue
        px = c[i]["close"]
        allow_long = (not use_filter) or px > trend[i]
        allow_short = (not use_filter) or px < trend[i]
        pos = None
        for j in range(i + 1, i + hold + 1):
            bar = c[j]
            ex = reason = None
            if pos is None:
                if j > i + window:
                    break
                if allow_long and bar["high"] > rh:
                    entry, side, stop = max(bar["open"], rh), 1, rl
                elif allow_short and bar["low"] < rl:
                    entry, side, stop = min(bar["open"], rl), -1, rh
                else:
                    continue
                risk = equity * risk_pct / 100
                qty = min(risk / abs(entry - stop), max_lev * equity / entry)  # exchange leverage cap
                pos = dict(entry=entry, side=side, stop=stop, tgt=entry + side * target_r * rng,
                           qty=qty, risk=qty * abs(entry - stop))
                # same-bar adverse check, conservative: stop before target
                if (side == 1 and bar["low"] <= stop) or (side == -1 and bar["high"] >= stop):
                    ex, reason = stop, "stop"
                elif (side == 1 and bar["high"] >= pos["tgt"]) or (side == -1 and bar["low"] <= pos["tgt"]):
                    ex, reason = pos["tgt"], "target"
            else:
                side = pos["side"]
                if (side == 1 and bar["low"] <= pos["stop"]) or (side == -1 and bar["high"] >= pos["stop"]):
                    ex = min(bar["open"], pos["stop"]) if side == 1 else max(bar["open"], pos["stop"]); reason = "stop"
                elif (side == 1 and bar["high"] >= pos["tgt"]) or (side == -1 and bar["low"] <= pos["tgt"]):
                    ex = max(bar["open"], pos["tgt"]) if side == 1 else min(bar["open"], pos["tgt"]); reason = "target"
                elif j == i + hold:
                    ex, reason = bar["close"], "time"
            if ex is not None:
                pnl = pos["qty"] * (ex - pos["entry"]) * pos["side"] - pos["qty"] * (pos["entry"] + ex) * fee / 200
                equity += pnl
                peak = max(peak, equity)
                dd = max(dd, (peak - equity) / peak * 100)
                trades.append(dict(t=c[j]["timestamp"].strftime("%Y-%m-%d %H:%M"), pnl=pnl, R=pnl / pos["risk"],
                                   reason=reason, side=pos["side"], year=c[j]["timestamp"].year))
                break
    return trades, equity, dd


def stats(trades, equity, dd):
    if not trades:
        return dict(n=0, wr=0, ret=0, pf=0, R=0, t=0, dd=0)
    w = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    l = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return dict(n=len(trades), wr=sum(1 for t in trades if t["pnl"] > 0) / len(trades), ret=equity - 100,
                pf=w / l if l else 99.0, R=sum(t["R"] for t in trades) / len(trades),
                t=tstat([t["R"] for t in trades]), dd=dd)


def fmt(s):
    return f"n{s['n']:>4} wr{s['wr']:>4.0%} {s['ret']:>+7.1f}% pf{s['pf']:>4.2f} R{s['R']:>+5.2f} t{s['t']:>+4.1f} dd{s['dd']:>4.1f}"


def run(pair, days, window, hold, fee, risk_pct, output):
    c = get_candles(pair, "1h", days)
    closes = [x["close"] for x in c]
    trend = sma(closes, 1200)
    a14 = atr([x["high"] for x in c], [x["low"] for x in c], closes, 14)
    by_dayhour = {(x["timestamp"].date(), x["timestamp"].hour): i for i, x in enumerate(c)}
    all_days = sorted({x["timestamp"].date() for x in c})
    print(f"\n{'=' * 118}")
    print(f"  SESSION ORB SWEEP — {pair} 1H  {c[0]['timestamp']:%Y-%m-%d} .. {c[-1]['timestamp']:%Y-%m-%d}   "
          f"window {window}h, hold {hold}h, fee {fee}%, risk {risk_pct}%")
    print(f"  select on {TRAIN[0]}-{TRAIN[1]}, evaluate on {TEST[0]}-{TEST[1]}")
    print(f"{'=' * 118}")

    results = {}
    for target_r in (1.0, 2.0):
        print(f"\n  --- trend-filtered, target {target_r:.0f}R ---")
        print(f"  {'H':>3}  {'TRAIN ' + TRAIN[0].__str__() + '-' + str(TRAIN[1]):<52}  {'TEST ' + str(TEST[0]) + '-' + str(TEST[1]):<52}")
        for H in range(24):
            kw = dict(window=window, hold=hold, target_r=target_r, use_filter=True, fee=fee, risk_pct=risk_pct)
            tr_tr, eq_tr, dd_tr = backtest(c, trend, a14, by_dayhour, all_days, H, years=TRAIN, **kw)
            tr_te, eq_te, dd_te = backtest(c, trend, a14, by_dayhour, all_days, H, years=TEST, **kw)
            s_tr, s_te = stats(tr_tr, eq_tr, dd_tr), stats(tr_te, eq_te, dd_te)
            results[(target_r, H)] = (s_tr, s_te)
            flag = " <" if s_tr["t"] > 2 else ""
            print(f"  {H:>3}  {fmt(s_tr)}  {fmt(s_te)}{flag}")

    # honest selection: best train hours by t-stat, report their test
    print(f"\n  --- selection: top 3 hours by TRAIN t-stat, per target ---")
    chosen = {}
    for target_r in (1.0, 2.0):
        ranked = sorted(((H, v) for (tr, H), v in results.items() if tr == target_r), key=lambda kv: kv[1][0]["t"], reverse=True)[:3]
        chosen[target_r] = [H for H, _ in ranked]
        for H, (s_tr, s_te) in ranked:
            print(f"  target {target_r:.0f}R  hour {H:>2} UTC   train t{s_tr['t']:+.1f} ret{s_tr['ret']:+.1f}%   ->   TEST {fmt(s_te)}")

    # for the chosen hours, show the unfiltered version and the year-by-year test
    print(f"\n  --- chosen hours without the trend filter (both directions allowed) ---")
    for target_r, hours in chosen.items():
        for H in hours:
            kw = dict(window=window, hold=hold, target_r=target_r, use_filter=False, fee=fee, risk_pct=risk_pct)
            tr_te, eq_te, dd_te = backtest(c, trend, a14, by_dayhour, all_days, H, years=TEST, **kw)
            print(f"  target {target_r:.0f}R  hour {H:>2}  no filter  TEST {fmt(stats(tr_te, eq_te, dd_te))}")

    print(f"\n  --- chosen hours, trend-filtered, TEST by year ---")
    for target_r, hours in chosen.items():
        for H in hours:
            kw = dict(window=window, hold=hold, target_r=target_r, use_filter=True, fee=fee, risk_pct=risk_pct)
            tr_te, eq_te, dd_te = backtest(c, trend, a14, by_dayhour, all_days, H, years=TEST, **kw)
            row = f"  target {target_r:.0f}R hour {H:>2}:"
            eq = 100.0
            for y in range(TEST[0], TEST[1] + 1):
                yt = [t for t in tr_te if t["year"] == y]
                yp = sum(t["pnl"] for t in yt)
                row += f"  {y} n{len(yt):>3} {yp / eq * 100:>+6.1f}%"
                eq += yp
            print(row)

    os.makedirs(output, exist_ok=True)
    path = os.path.join(output, f"session_orb_{pair.replace('-', '_')}.json")
    with open(path, "w") as f:
        json.dump({"pair": pair, "window": window, "hold": hold, "fee": fee,
                   "sweep": {f"{tr}R_h{H}": {"train": a, "test": b} for (tr, H), (a, b) in results.items()},
                   "chosen": {f"{k}R": v for k, v in chosen.items()}}, f, indent=2)
    print(f"\n  Saved: {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="ETH-USD")
    p.add_argument("--days", type=int, default=3400)
    p.add_argument("--window", type=int, default=3)
    p.add_argument("--hold", type=int, default=8)
    p.add_argument("--fee", type=float, default=0.10)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--output", default="crypto_analysis/output")
    a = p.parse_args()
    run(a.pair, a.days, a.window, a.hold, a.fee, a.risk, a.output)


if __name__ == "__main__":
    main()
