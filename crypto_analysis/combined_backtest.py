"""
Combined regime-switching backtest.

  ADX >= 25  ->  TREND:  EMA9/SMA20 cross in direction of SMA200, exit on
                         opposite cross or close through SMA20
  ADX <  25  ->  RANGE:  Bollinger mean-reversion: pierce, wait for close
                         back inside band, enter against, stop 2xATR,
                         target SMA20, time stop 20 bars

One position at a time. Each trade is managed by the rules of the strategy
that opened it, even if the regime flips mid-trade.

Usage:
    python -m crypto_analysis.combined_backtest --pair ETH-USD --granularity 6h --days 365
"""

import argparse
import json
import os
from datetime import datetime

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import sma, ema, atr, adx, bollinger_bands

ADX_SPLIT = 25
ATR_MULT = 2.0
BB_MAX_BARS = 20


def backtest(candles, mode):
    """mode: 'combined' | 'trend_only' | 'range_only'"""
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]

    e9, s20, s200 = ema(closes, 9), sma(closes, 20), sma(closes, 200)
    a14 = atr(highs, lows, closes, 14)
    x14 = adx(highs, lows, closes, 14)
    bb = bollinger_bands(closes, 20, 2.0)

    trades, pos, pierced = [], None, None
    start = next((i for i, v in enumerate(s200) if v is not None), len(candles))

    for i in range(max(start, 21), len(candles)):
        px, up, mid, lo = closes[i], bb["upper"][i], bb["middle"][i], bb["lower"][i]
        ax = x14["adx"][i]
        if None in (e9[i], s20[i], s200[i], a14[i], ax, up, e9[i - 1], s20[i - 1]):
            continue
        ts = candles[i]["timestamp"].strftime("%Y-%m-%d %H:%M")
        regime = "TREND" if ax >= ADX_SPLIT else "RANGE"
        cross_up = e9[i - 1] <= s20[i - 1] and e9[i] > s20[i]
        cross_dn = e9[i - 1] >= s20[i - 1] and e9[i] < s20[i]

        # ---------------- manage open position ----------------
        if pos:
            exit_px = reason = None
            bars = i - pos["i"]
            if pos["strategy"] == "TREND":
                if pos["side"] == "LONG" and (cross_dn or px < s20[i]):
                    exit_px, reason = px, "cross" if cross_dn else "sma20"
                elif pos["side"] == "SHORT" and (cross_up or px > s20[i]):
                    exit_px, reason = px, "cross" if cross_up else "sma20"
            else:  # RANGE
                if pos["side"] == "LONG":
                    if lows[i] <= pos["stop"]:
                        exit_px, reason = min(opens[i], pos["stop"]), "stop"
                    elif highs[i] >= mid:
                        exit_px, reason = max(opens[i], mid), "target"
                else:
                    if highs[i] >= pos["stop"]:
                        exit_px, reason = max(opens[i], pos["stop"]), "stop"
                    elif lows[i] <= mid:
                        exit_px, reason = min(opens[i], mid), "target"
                if exit_px is None and bars >= BB_MAX_BARS:
                    exit_px, reason = px, "time"
            if exit_px is not None:
                pnl = (exit_px - pos["px"]) / pos["px"] * 100 * (1 if pos["side"] == "LONG" else -1)
                trades.append({**pos, "exit": round(exit_px, 2), "exit_time": ts, "pnl": round(pnl, 3),
                               "reason": reason, "bars": bars, "exit_regime": regime})
                pos = None
            continue

        # ---------------- entries ----------------
        side = strategy = None
        if regime == "TREND":
            pierced = None  # a pierce during a trend is a band walk, not a reversal setup

        if regime == "TREND" and mode in ("combined", "trend_only"):
            near_e9 = abs(px - e9[i]) / e9[i] < 0.004
            bull_c, bear_c = px > opens[i], px < opens[i]
            if px > s200[i]:
                if cross_up or (near_e9 and bull_c and e9[i] > s20[i]):
                    side, strategy = "LONG", "TREND"
            elif px < s200[i]:
                if cross_dn or (near_e9 and bear_c and e9[i] < s20[i]):
                    side, strategy = "SHORT", "TREND"

        elif regime == "RANGE" and mode in ("combined", "range_only"):
            if px < lo:
                pierced = "low"
            elif px > up:
                pierced = "high"
            elif pierced == "low" and px >= lo:
                side, strategy, pierced = "LONG", "RANGE", None
            elif pierced == "high" and px <= up:
                side, strategy, pierced = "SHORT", "RANGE", None

        if side:
            stop = px - ATR_MULT * a14[i] if side == "LONG" else px + ATR_MULT * a14[i]
            pos = {"i": i, "side": side, "strategy": strategy, "px": round(px, 2), "entry_time": ts,
                   "stop": round(stop, 2), "adx": round(ax, 1), "entry_regime": regime}

    return trades


def stats(trades):
    if not trades:
        return {"n": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq = peak = 100.0
    dd = 0.0
    for p in pnls:
        eq *= 1 + p / 100
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100)
    gw, gl = sum(wins), -sum(losses)
    return {"n": len(trades), "win_rate": round(len(wins) / len(trades), 3),
            "total_pnl": round(sum(pnls), 2), "avg_pnl": round(sum(pnls) / len(pnls), 3),
            "avg_win": round(gw / len(wins), 3) if wins else 0,
            "avg_loss": round(-gl / len(losses), 3) if losses else 0,
            "profit_factor": round(gw / gl, 2) if gl else float("inf"),
            "max_dd": round(dd, 2), "final_equity": round(eq, 2)}


def fmt(name, s):
    if s["n"] == 0:
        return f"  {name:<22} {'0':>4}"
    return (f"  {name:<22} {s['n']:>4} {s['win_rate']:>4.0%} {s['total_pnl']:>+8.2f}% "
            f"{s['avg_win']:>+5.2f} {s['avg_loss']:>+5.2f} {s['profit_factor']:>5} {s['max_dd']:>5.1f}% ${s['final_equity']:>7.2f}")


def run(pair, granularity, days, output_dir):
    print(f"Fetching {pair} {granularity}, {days} days...")
    candles = get_candles(pair, granularity, days)
    print(f"  -> {len(candles)} candles")

    res = {m: backtest(candles, m) for m in ("trend_only", "range_only", "combined")}

    print(f"\n{'=' * 84}")
    print(f"  REGIME SWITCHING — {pair} {granularity}, {days}d  (ADX split {ADX_SPLIT})")
    print(f"{'=' * 84}")
    print(f"  {'strategy':<22} {'n':>4} {'WR':>5} {'PnL':>9} {'avgW':>6} {'avgL':>6} {'PF':>5} {'DD':>6} {'equity':>8}")
    print(f"  {'-' * 80}")
    print(fmt("TREND only (ADX>=25)", stats(res["trend_only"])))
    print(fmt("RANGE only (ADX<25)", stats(res["range_only"])))
    print(fmt("COMBINED", stats(res["combined"])))

    comb = res["combined"]
    print(f"\n  --- COMBINED, split by strategy that opened the trade ---")
    for strat in ("TREND", "RANGE"):
        print(fmt(f"  {strat}", stats([t for t in comb if t["strategy"] == strat])))
    print(f"\n  --- COMBINED, by side ---")
    for side in ("LONG", "SHORT"):
        print(fmt(f"  {side}", stats([t for t in comb if t["side"] == side])))

    # Did the two strategies step on each other? Count trades lost vs standalone.
    print(f"\n  Standalone trend trades: {len(res['trend_only'])}, in combined: {sum(1 for t in comb if t['strategy']=='TREND')}")
    print(f"  Standalone range trades: {len(res['range_only'])}, in combined: {sum(1 for t in comb if t['strategy']=='RANGE')}")

    # Equity curve by month
    print(f"\n  --- COMBINED equity by month ---")
    eq, month, meq = 100.0, None, 100.0
    rows = []
    for t in comb:
        m = t["exit_time"][:7]
        if month and m != month:
            rows.append((month, meq, eq))
            meq = eq
        month = m
        eq *= 1 + t["pnl"] / 100
    if month:
        rows.append((month, meq, eq))
    for m, a, b in rows:
        bar = "+" * int(max(0, b - a) / 2) if b >= a else "-" * int((a - b) / 2)
        print(f"  {m}  {a:>7.2f} -> {b:>7.2f}  {(b/a-1)*100:>+6.2f}%  {bar}")

    print(f"\n  --- COMBINED, last 12 trades ---")
    for t in comb[-12:]:
        print(f"  {t['entry_time']} {t['strategy']:<5} {t['side']:<5} in {t['px']:>8.2f} out {t['exit']:>8.2f} "
              f"{t['pnl']:>+6.2f}% {t['reason']:<6} {t['bars']:>2}b adx {t['adx']}")

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"combined_{pair.replace('-', '_')}_{granularity}_{days}d.json")
    with open(path, "w") as f:
        json.dump({"pair": pair, "granularity": granularity, "days": days, "adx_split": ADX_SPLIT,
                   "generated": datetime.utcnow().isoformat(),
                   "stats": {m: stats(t) for m, t in res.items()},
                   "trades": res}, f, indent=2)
    print(f"\n  Saved: {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="ETH-USD")
    p.add_argument("--granularity", default="6h", choices=["15m", "1h", "6h", "1d"])
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--output", default="crypto_analysis/output")
    a = p.parse_args()
    run(a.pair, a.granularity, a.days, a.output)


if __name__ == "__main__":
    main()
