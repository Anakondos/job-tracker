"""
System v1 — regime-switching system built from everything measured so far.

Timeframe: 6H only (1H dies to fees, 1D walks the band).

Regime (ADX14):
  RANGE  ADX < split
  TREND  ADX >= split AND ADX rising vs `adx_rising_bars` ago
  (ADX >= split but falling = fading trend -> no new entries)

RANGE leg  Bollinger(20,2) pierce, wait for close back inside, enter against.
           Stop 2xATR. Target = entry + target_frac * (SMA20 - entry).
           Rationale: measured retracements reach ~60-70% of the move; the
           half-way target has higher expectancy and half the drawdown.
TREND leg  Direction by SMA200. Entry on EMA9/SMA20 cross or pullback to EMA9.
           Hard stop 2xATR, then trail with Parabolic SAR.

Filters    No entry when ATR14 > vol_spike_mult x ATR(50-bar avg): the quiet
           range that precedes a crash is where mean reversion dies.
           After `loss_pause_after` consecutive losses, no entries for
           `loss_pause_bars` bars.

Risk       Fixed fractional: risk_pct of current equity per trade, sized by
           stop distance. Fees on notional both ways. Equity compounds.

Validation Per-year breakdown, buy&hold benchmark, parameter grid, second asset.

Usage:
    python -m crypto_analysis.system_v1 --pair ETH-USD --days 1095
    python -m crypto_analysis.system_v1 --pair BTC-USD --days 1095 --grid
"""

import argparse
import itertools
import json
import os
from datetime import datetime

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import sma, ema, atr, adx, bollinger_bands, parabolic_sar

DEFAULTS = dict(
    adx_split=25, adx_rising_bars=3,
    atr_mult=2.0, target_frac=0.5, bb_max_bars=20,
    vol_spike_mult=2.0,
    loss_pause_after=3, loss_pause_bars=10,
    risk_pct=1.0, fee_rt_pct=0.10,
    trend_exit="sar",  # 'sar' (trail on SAR flip) or 'sma20' (EMA9/SMA20 cross or close through SMA20)
)


def features(candles):
    c = [x["close"] for x in candles]
    h = [x["high"] for x in candles]
    l = [x["low"] for x in candles]
    a14 = atr(h, l, c, 14)
    return dict(
        o=[x["open"] for x in candles], h=h, l=l, c=c,
        e9=ema(c, 9), s20=sma(c, 20), s200=sma(c, 200),
        bb=bollinger_bands(c, 20, 2.0),
        atr=a14, atr_avg=sma([v if v else 0.0 for v in a14], 50),
        adx=adx(h, l, c, 14), sar=parabolic_sar(h, l),
    )


def run_system(candles, F, p):
    o, h, l, c = F["o"], F["h"], F["l"], F["c"]
    e9, s20, s200, bb = F["e9"], F["s20"], F["s200"], F["bb"]
    a14, a_avg, X, sar = F["atr"], F["atr_avg"], F["adx"]["adx"], F["sar"]
    rb = p["adx_rising_bars"]

    equity, peak, max_dd = 100.0, 100.0, 0.0
    trades, pos, pierced = [], None, None
    consec_losses, pause_until = 0, -1
    start = next((i for i in range(len(c)) if s200[i] and a_avg[i] and a_avg[i] > 0 and X[i]), len(c))

    for i in range(max(start, rb + 1), len(c)):
        up, mid, lo = bb["upper"][i], bb["middle"][i], bb["lower"][i]
        if None in (e9[i], s20[i], s200[i], a14[i], X[i], X[i - rb], up, sar[i], e9[i - 1], s20[i - 1]):
            continue
        ts = candles[i]["timestamp"]
        px = c[i]
        if X[i] < p["adx_split"]:
            regime = "RANGE"
        elif X[i] > X[i - rb]:
            regime = "TREND"
        else:
            regime = "FADE"
        cross_up = e9[i - 1] <= s20[i - 1] and e9[i] > s20[i]
        cross_dn = e9[i - 1] >= s20[i - 1] and e9[i] < s20[i]

        # ---------------- manage position ----------------
        if pos:
            d = 1 if pos["side"] == "LONG" else -1
            bars = i - pos["i"]
            exit_px = reason = None
            if pos["leg"] == "RANGE":
                tgt = pos["px"] + (pos["mid"] - pos["px"]) * p["target_frac"]
                if d == 1:
                    if l[i] <= pos["stop"]:
                        exit_px, reason = min(o[i], pos["stop"]), "stop"
                    elif h[i] >= tgt:
                        exit_px, reason = max(o[i], tgt), "target"
                else:
                    if h[i] >= pos["stop"]:
                        exit_px, reason = max(o[i], pos["stop"]), "stop"
                    elif l[i] <= tgt:
                        exit_px, reason = min(o[i], tgt), "target"
                if exit_px is None and bars >= p["bb_max_bars"]:
                    exit_px, reason = px, "time"
            else:  # TREND: hard stop, then trail (SAR flip or SMA20 cross)
                if d == 1 and l[i] <= pos["stop"]:
                    exit_px, reason = min(o[i], pos["stop"]), "stop"
                elif d == -1 and h[i] >= pos["stop"]:
                    exit_px, reason = max(o[i], pos["stop"]), "stop"
                elif p["trend_exit"] == "sar":
                    if (d == 1 and sar[i]["trend"] == "bear") or (d == -1 and sar[i]["trend"] == "bull"):
                        exit_px, reason = px, "sar"
                elif (d == 1 and (cross_dn or px < s20[i])) or (d == -1 and (cross_up or px > s20[i])):
                    exit_px, reason = px, "sma20"
            if exit_px is not None:
                gross = pos["qty"] * (exit_px - pos["px"]) * d
                fee = pos["qty"] * (pos["px"] + exit_px) * p["fee_rt_pct"] / 200
                pnl = gross - fee
                equity += pnl
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                r = pnl / pos["risk"]
                consec_losses = consec_losses + 1 if pnl <= 0 else 0
                if consec_losses >= p["loss_pause_after"]:
                    pause_until, consec_losses = i + p["loss_pause_bars"], 0
                trades.append({**pos, "exit": round(exit_px, 2), "exit_time": ts.strftime("%Y-%m-%d %H:%M"),
                               "entry_time": pos["entry_time"].strftime("%Y-%m-%d %H:%M"),
                               "pnl_usd": round(pnl, 3), "pnl_pct": round(pnl / (equity - pnl) * 100, 3),
                               "R": round(r, 2), "reason": reason, "bars": bars,
                               "equity": round(equity, 2), "year": ts.year})
                pos = None
            continue

        # ---------------- entries ----------------
        if regime != "RANGE":
            pierced = None
        if i < pause_until:
            continue
        if a14[i] > p["vol_spike_mult"] * a_avg[i]:
            pierced = None
            continue

        side = leg = None
        if regime == "TREND":
            near = abs(px - e9[i]) / e9[i] < 0.004
            if px > s200[i] and (cross_up or (near and px > o[i] and e9[i] > s20[i])):
                side, leg = "LONG", "TREND"
            elif px < s200[i] and (cross_dn or (near and px < o[i] and e9[i] < s20[i])):
                side, leg = "SHORT", "TREND"
        elif regime == "RANGE":
            if px < lo:
                pierced = "low"
            elif px > up:
                pierced = "high"
            elif pierced == "low" and px >= lo:
                side, leg, pierced = "LONG", "RANGE", None
            elif pierced == "high" and px <= up:
                side, leg, pierced = "SHORT", "RANGE", None

        if side:
            d = 1 if side == "LONG" else -1
            stop = px - d * p["atr_mult"] * a14[i]
            risk = equity * p["risk_pct"] / 100
            qty = risk / abs(px - stop)
            pos = dict(i=i, side=side, leg=leg, px=round(px, 2), stop=round(stop, 2), mid=mid,
                       qty=qty, risk=risk, entry_time=ts, adx=round(X[i], 1), regime=regime)

    return trades, equity, max_dd


def stats(trades, eq_start=100.0, eq_end=None):
    if not trades:
        return {"n": 0}
    pnls = [t["pnl_usd"] for t in trades]
    w = [x for x in pnls if x > 0]
    lo = [x for x in pnls if x <= 0]
    eq = eq_start
    peak, dd = eq, 0.0
    for x in pnls:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100)
    end = eq_end if eq_end is not None else eq
    return {"n": len(trades), "win_rate": round(len(w) / len(trades), 3),
            "return_pct": round((end / eq_start - 1) * 100, 2),
            "profit_factor": round(sum(w) / -sum(lo), 2) if lo else float("inf"),
            "avg_R": round(sum(t["R"] for t in trades) / len(trades), 3),
            "max_dd": round(dd, 2),
            "exits": {k: sum(1 for t in trades if t["reason"] == k) for k in ("target", "stop", "sar", "sma20", "time")}}


def fmt(name, s):
    if s["n"] == 0:
        return f"  {name:<22} {'0':>4}"
    ex = " ".join(f"{k}:{v}" for k, v in s["exits"].items() if v)
    return (f"  {name:<22} {s['n']:>4} {s['win_rate']:>4.0%} {s['return_pct']:>+8.2f}% "
            f"{s['profit_factor']:>5} {s['avg_R']:>+6.2f}R {s['max_dd']:>5.1f}%  {ex}")


HDR = f"  {'':<22} {'n':>4} {'WR':>5} {'return':>9} {'PF':>5} {'avg R':>7} {'DD':>6}  exits"


def report(pair, candles, trades, equity, max_dd, p):
    print(f"\n{'=' * 90}")
    print(f"  SYSTEM v1 — {pair} 6H  {candles[0]['timestamp']:%Y-%m-%d} .. {candles[-1]['timestamp']:%Y-%m-%d}")
    print(f"  risk {p['risk_pct']}%/trade, fee {p['fee_rt_pct']}% RT, ADX split {p['adx_split']}, "
          f"ATR x{p['atr_mult']}, target {p['target_frac']:.0%} to mid, vol spike x{p['vol_spike_mult']}")
    print(f"{'=' * 90}")
    print(HDR)
    print(f"  {'-' * 86}")
    print(fmt("ALL", stats(trades, 100.0, equity)))
    for leg in ("TREND", "RANGE"):
        print(fmt(f"  {leg}", stats([t for t in trades if t["leg"] == leg])))
    for side in ("LONG", "SHORT"):
        print(fmt(f"  {side}", stats([t for t in trades if t["side"] == side])))
    print(f"  final equity ${equity:.2f}, max drawdown {max_dd:.2f}% (on running equity)")

    print(f"\n  --- by year (system vs buy & hold) ---")
    print(f"  {'year':<6} {'n':>4} {'WR':>5} {'return':>9} {'PF':>5} {'DD':>6} {'B&H':>9}   {'equity':>8}")
    years = sorted({t["year"] for t in trades})
    eq_prev = 100.0
    for y in years:
        yt = [t for t in trades if t["year"] == y]
        eq_end = yt[-1]["equity"]
        s = stats(yt, eq_prev, eq_end)
        yc = [x for x in candles if x["timestamp"].year == y]
        bh = (yc[-1]["close"] / yc[0]["close"] - 1) * 100 if yc else 0
        print(f"  {y:<6} {s['n']:>4} {s['win_rate']:>4.0%} {s['return_pct']:>+8.2f}% {s['profit_factor']:>5} "
              f"{s['max_dd']:>5.1f}% {bh:>+8.1f}%   ${eq_end:>7.2f}")
        eq_prev = eq_end

    print(f"\n  --- worst 5 trades (R) ---")
    for t in sorted(trades, key=lambda t: t["R"])[:5]:
        print(f"  {t['entry_time']} {t['leg']:<5} {t['side']:<5} {t['R']:>+6.2f}R {t['pnl_pct']:>+6.2f}% {t['reason']:<6} adx {t['adx']}")
    print(f"\n  --- last 8 trades ---")
    for t in trades[-8:]:
        print(f"  {t['entry_time']} {t['leg']:<5} {t['side']:<5} in {t['px']:>9.2f} out {t['exit']:>9.2f} "
              f"{t['R']:>+6.2f}R {t['reason']:<6} {t['bars']:>2}b eq ${t['equity']:.2f}")


def grid(pair, candles, F, p):
    print(f"\n  --- parameter grid: {pair} (return% / DD% / PF / n) ---")
    print(f"  {'':>10}" + "".join(f"{'tgt ' + str(int(tf * 100)) + '%':>22}" for tf in (0.4, 0.5, 0.6)))
    for a_split in (20, 25, 30):
        for am in (1.5, 2.0, 2.5):
            row = f"  ADX{a_split} ATR{am}"
            for tf in (0.4, 0.5, 0.6):
                q = {**p, "adx_split": a_split, "atr_mult": am, "target_frac": tf}
                tr, eq, dd = run_system(candles, F, q)
                s = stats(tr, 100.0, eq)
                mark = "*" if (a_split, am, tf) == (p["adx_split"], p["atr_mult"], p["target_frac"]) else " "
                row += f"  {mark}{s['return_pct']:>+6.1f}/{dd:>4.1f}/{s['profit_factor']:>4}/{s['n']:>3}"
            print(row)
    print("  * = default params")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="ETH-USD")
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--output", default="crypto_analysis/output")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = ap.parse_args()
    p = {k: getattr(a, k) for k in DEFAULTS}

    print(f"Fetching {a.pair} 6h, {a.days} days...")
    candles = get_candles(a.pair, "6h", a.days)
    print(f"  -> {len(candles)} candles")
    F = features(candles)
    trades, equity, max_dd = run_system(candles, F, p)
    report(a.pair, candles, trades, equity, max_dd, p)
    if a.grid:
        grid(a.pair, candles, F, p)

    os.makedirs(a.output, exist_ok=True)
    path = os.path.join(a.output, f"system_v1_{a.pair.replace('-', '_')}_{a.days}d.json")
    with open(path, "w") as f:
        json.dump({"pair": a.pair, "days": a.days, "params": p, "generated": datetime.utcnow().isoformat(),
                   "final_equity": round(equity, 2), "max_dd": round(max_dd, 2),
                   "stats": stats(trades, 100.0, equity), "trades": trades}, f, indent=2, default=str)
    print(f"\n  Saved: {path}")


if __name__ == "__main__":
    main()
