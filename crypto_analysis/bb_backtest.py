"""
Bollinger Band mean-reversion backtest: enter on band pierce, exit at middle line.

Variants:
  A  raw        close outside band -> enter against move, exit at SMA20, no stop (time stop only)
  B  atr_stop   A + stop at 2x ATR beyond entry
  C  adx_range  B + only when ADX < 25 (range regime)
  D  confirm    enter on close BACK INSIDE band after a pierce, ATR stop, exit at SMA20

Usage:
    python -m crypto_analysis.bb_backtest --pair ETH-USD --granularity 1h --days 90
    python -m crypto_analysis.bb_backtest --pair ETH-USD --granularity 1d --days 730 --atr-mult 1.5
"""

import argparse
import json
import os
from datetime import datetime

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import bollinger_bands, atr, adx

MAX_BARS = 20  # time stop: one BB period


def run_variant(candles, bb, atr14, adx14, *, use_atr_stop, atr_mult, adx_max, confirm_entry):
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    opens = [c["open"] for c in candles]

    trades = []
    pos = None
    pierced = None  # for confirm mode: 'low'/'high' pending re-entry

    for i in range(21, len(candles)):
        up, mid, lo = bb["upper"][i], bb["middle"][i], bb["lower"][i]
        a = atr14[i]
        ax = adx14["adx"][i]
        if up is None or a is None:
            continue
        ts = candles[i]["timestamp"].strftime("%Y-%m-%d %H:%M")

        # ---- manage open position ----
        if pos:
            bars = i - pos["i"]
            exit_px, reason = None, None
            if pos["side"] == "LONG":
                if use_atr_stop and lows[i] <= pos["stop"]:
                    exit_px, reason = min(opens[i], pos["stop"]), "stop"
                elif highs[i] >= mid:
                    exit_px, reason = max(opens[i], mid) if opens[i] >= mid else mid, "target"
            else:
                if use_atr_stop and highs[i] >= pos["stop"]:
                    exit_px, reason = max(opens[i], pos["stop"]), "stop"
                elif lows[i] <= mid:
                    exit_px, reason = min(opens[i], mid) if opens[i] <= mid else mid, "target"
            if exit_px is None and bars >= MAX_BARS:
                exit_px, reason = closes[i], "time"
            if exit_px is not None:
                pnl = (exit_px - pos["px"]) / pos["px"] * 100
                if pos["side"] == "SHORT":
                    pnl = -pnl
                trades.append({**pos, "exit": round(exit_px, 2), "exit_time": ts, "pnl": round(pnl, 3),
                               "reason": reason, "bars": bars})
                pos = None
            continue

        # ---- entry ----
        if adx_max is not None and (ax is None or ax >= adx_max):
            pierced = None
            continue

        side = None
        if confirm_entry:
            # remember pierce, enter when close comes back inside
            if closes[i] < lo:
                pierced = "low"
            elif closes[i] > up:
                pierced = "high"
            elif pierced == "low" and closes[i] >= lo:
                side, pierced = "LONG", None
            elif pierced == "high" and closes[i] <= up:
                side, pierced = "SHORT", None
        else:
            if closes[i] < lo:
                side = "LONG"
            elif closes[i] > up:
                side = "SHORT"

        if side:
            px = closes[i]
            stop = px - atr_mult * a if side == "LONG" else px + atr_mult * a
            pos = {"i": i, "side": side, "px": round(px, 2), "entry_time": ts,
                   "stop": round(stop, 2), "mid_at_entry": round(mid, 2),
                   "dist_to_mid_pct": round(abs(mid - px) / px * 100, 2)}

    return trades


def stats(trades):
    if not trades:
        return {"n": 0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    eq, peak, dd = 100.0, 100.0, 0.0
    for p in pnls:
        eq *= 1 + p / 100
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100)
    gross_w = sum(wins)
    gross_l = -sum(losses)
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "n": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades), 3),
        "total_pnl": round(sum(pnls), 2), "avg_pnl": round(sum(pnls) / len(pnls), 3),
        "avg_win": round(gross_w / len(wins), 3) if wins else 0,
        "avg_loss": round(-gross_l / len(losses), 3) if losses else 0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else float("inf"),
        "max_dd": round(dd, 2), "final_equity": round(eq, 2),
        "avg_bars": round(sum(t["bars"] for t in trades) / len(trades), 1),
        "exits": reasons,
    }


def run(pair, granularity, days, atr_mult, output_dir):
    print(f"Fetching {pair} {granularity}, {days} days...")
    candles = get_candles(pair, granularity, days)
    print(f"  -> {len(candles)} candles")
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    bb = bollinger_bands(closes, 20, 2.0)
    a14 = atr(highs, lows, closes, 14)
    x14 = adx(highs, lows, closes, 14)

    variants = {
        "A raw (no stop)":        dict(use_atr_stop=False, atr_mult=atr_mult, adx_max=None, confirm_entry=False),
        "B + ATR stop":           dict(use_atr_stop=True,  atr_mult=atr_mult, adx_max=None, confirm_entry=False),
        "C + ADX<25 range only":  dict(use_atr_stop=True,  atr_mult=atr_mult, adx_max=25,   confirm_entry=False),
        "D confirm re-entry":     dict(use_atr_stop=True,  atr_mult=atr_mult, adx_max=None, confirm_entry=True),
        "E confirm + ADX<25":     dict(use_atr_stop=True,  atr_mult=atr_mult, adx_max=25,   confirm_entry=True),
    }

    results = {}
    print(f"\n{'=' * 78}")
    print(f"  BOLLINGER MEAN-REVERSION — {pair} {granularity}, {days}d, stop={atr_mult}xATR, time stop {MAX_BARS} bars")
    print(f"{'=' * 78}")
    print(f"  {'variant':<24} {'n':>4} {'WR':>5} {'PnL':>8} {'avgW':>6} {'avgL':>6} {'PF':>5} {'DD':>6} {'bars':>5}  exits")
    print(f"  {'-' * 76}")
    for name, kw in variants.items():
        tr = run_variant(candles, bb, a14, x14, **kw)
        s = stats(tr)
        results[name] = {"stats": s, "trades": tr}
        if s["n"] == 0:
            print(f"  {name:<24} {'0':>4}")
            continue
        ex = " ".join(f"{k}:{v}" for k, v in sorted(s["exits"].items()))
        print(f"  {name:<24} {s['n']:>4} {s['win_rate']:>4.0%} {s['total_pnl']:>+7.2f}% "
              f"{s['avg_win']:>+5.2f} {s['avg_loss']:>+5.2f} {s['profit_factor']:>5} {s['max_dd']:>5.1f}% {s['avg_bars']:>5}  {ex}")

    # long vs short split for best-looking variant (by PF with n>=10)
    best = max((k for k, v in results.items() if v["stats"]["n"] >= 10),
               key=lambda k: results[k]["stats"]["profit_factor"], default=None)
    if best:
        tr = results[best]["trades"]
        print(f"\n  --- {best}: by side ---")
        for side in ("LONG", "SHORT"):
            s = stats([t for t in tr if t["side"] == side])
            if s["n"]:
                print(f"  {side:<6} n={s['n']:>3} WR={s['win_rate']:.0%} PnL={s['total_pnl']:+.2f}% PF={s['profit_factor']}")
        print(f"\n  --- {best}: last 10 trades ---")
        for t in tr[-10:]:
            print(f"  {t['entry_time']} {t['side']:<5} in {t['px']:>8.2f} out {t['exit']:>8.2f} "
                  f"{t['pnl']:>+6.2f}% {t['reason']:<6} {t['bars']:>2}b  mid-dist {t['dist_to_mid_pct']}%")

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"bb_backtest_{pair.replace('-', '_')}_{granularity}_{days}d.json")
    with open(path, "w") as f:
        json.dump({"pair": pair, "granularity": granularity, "days": days, "atr_mult": atr_mult,
                   "generated": datetime.utcnow().isoformat(),
                   "variants": {k: v["stats"] for k, v in results.items()},
                   "trades": {k: v["trades"] for k, v in results.items()}}, f, indent=2)
    print(f"\n  Saved: {path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="ETH-USD")
    p.add_argument("--granularity", default="1h", choices=["5m", "15m", "1h", "6h", "1d"])
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--atr-mult", type=float, default=2.0)
    p.add_argument("--output", default="crypto_analysis/output")
    a = p.parse_args()
    run(a.pair, a.granularity, a.days, a.atr_mult, a.output)


if __name__ == "__main__":
    main()
