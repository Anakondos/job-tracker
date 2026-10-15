"""
What a 90-99% win rate costs. Five constructions that deliver the win rate
on demand, run over the full Coinbase 1H history so the tail has room to show.

  A  tiny target toward SMA20 with a wide (or no) stop
  B  grid / martingale: average down against the move, take profit from the average

All of them lose 80-99% of capital over 9 years while winning 77-99% of trades.

Usage:
    python -m crypto_analysis.winrate_trap --pair ETH-USD --days 3400
"""

import argparse

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import bollinger_bands, atr

FEE = 0.10


def summarize(name, trades, equity, dd, n_days, worst_expo=None):
    pn = [t["pnl"] for t in trades]
    w = [x for x in pn if x > 0]
    l = [x for x in pn if x <= 0]
    avg_w = sum(w) / len(w) if w else 0
    avg_l = sum(l) / len(l) if l else 0
    pf = sum(w) / -sum(l) if l else 99
    print(f"\n  {name}")
    print(f"    trades {len(trades)} ({len(trades) / n_days:.1f}/day)  WR {len(w) / len(trades):.1%}  "
          f"return {equity - 100:+.1f}%  maxDD {dd:.1f}%  PF {pf:.2f}")
    print(f"    avg win {avg_w:+.3f}%  avg loss {avg_l:+.3f}%  worst {min(pn):+.2f}%  "
          f"one loss erases {(-avg_l / avg_w) if avg_w else 0:.0f} wins"
          + (f"  max exposure {worst_expo:.0f}% of equity" if worst_expo else ""))
    print("    worst 5:", ", ".join(f"{t['t']:%Y-%m-%d} {t['pnl']:+.2f}%" for t in sorted(trades, key=lambda t: t["pnl"])[:5]))


def run(pair, days):
    c = get_candles(pair, "1h", days)
    C = [x["close"] for x in c]; H = [x["high"] for x in c]; L = [x["low"] for x in c]; O = [x["open"] for x in c]
    bb = bollinger_bands(C, 20, 2.0); A = atr(H, L, C, 14)
    n_days = (c[-1]["timestamp"] - c[0]["timestamp"]).days or 1
    print(f"\n{pair} 1H: {len(c)} candles, {c[0]['timestamp']:%Y-%m-%d} .. {c[-1]['timestamp']:%Y-%m-%d} ({n_days} days)")

    def tiny(name, target_frac, stop_mult, max_bars, risk_pct=1.0, notional_pct=20.0):
        equity, peak, dd, trades, pos, pierced = 100.0, 100.0, 0.0, [], None, None
        for i in range(21, len(C)):
            up, mid, lo, a = bb["upper"][i], bb["middle"][i], bb["lower"][i], A[i]
            if None in (up, a):
                continue
            if pos:
                d, bars = pos["d"], i - pos["i"]
                tgt = pos["px"] + (pos["mid"] - pos["px"]) * target_frac
                ex = None
                if d == 1:
                    if pos["stop"] and L[i] <= pos["stop"]: ex = min(O[i], pos["stop"])
                    elif H[i] >= tgt: ex = max(O[i], tgt)
                else:
                    if pos["stop"] and H[i] >= pos["stop"]: ex = max(O[i], pos["stop"])
                    elif L[i] <= tgt: ex = min(O[i], tgt)
                if ex is None and bars >= max_bars:
                    ex = C[i]
                if ex is not None:
                    pnl = pos["qty"] * (ex - pos["px"]) * d - pos["qty"] * (pos["px"] + ex) * FEE / 200
                    equity += pnl; peak = max(peak, equity); dd = max(dd, (peak - equity) / peak * 100)
                    trades.append(dict(t=c[i]["timestamp"], pnl=pnl / (equity - pnl) * 100))
                    pos = None
                continue
            d = 0
            if C[i] < lo: pierced = "low"
            elif C[i] > up: pierced = "high"
            elif pierced == "low" and C[i] >= lo: d, pierced = 1, None
            elif pierced == "high" and C[i] <= up: d, pierced = -1, None
            if d:
                if stop_mult:
                    stop = C[i] - d * stop_mult * a; qty = equity * risk_pct / 100 / abs(C[i] - stop)
                else:
                    stop = None; qty = equity * notional_pct / 100 / C[i]
                pos = dict(i=i, px=C[i], d=d, stop=stop, mid=mid, qty=qty)
        summarize(name, trades, equity, dd, n_days)

    def grid(name, adds, step_mult, tp_pct, max_bars, notional_pct):
        equity, peak, dd, trades, pos, worst_expo = 100.0, 100.0, 0.0, [], None, 0.0
        for i in range(21, len(C)):
            up, lo, a = bb["upper"][i], bb["lower"][i], A[i]
            if None in (up, a):
                continue
            if pos:
                d = pos["d"]; avg = pos["cost"] / pos["qty"]; bars = i - pos["i"]
                tp = avg * (1 + d * tp_pct / 100)
                ex = None
                if (d == 1 and H[i] >= tp) or (d == -1 and L[i] <= tp):
                    ex = max(O[i], tp) if d == 1 else min(O[i], tp)
                elif bars >= max_bars:
                    ex = C[i]
                else:
                    worst_expo = max(worst_expo, pos["cost"] / equity * 100)
                    if pos["legs"] <= adds and ((d == 1 and C[i] <= pos["last"] - step_mult * pos["atr"]) or
                                                (d == -1 and C[i] >= pos["last"] + step_mult * pos["atr"])):
                        q = pos["leg"] / C[i]
                        pos["qty"] += q; pos["cost"] += q * C[i]; pos["legs"] += 1; pos["last"] = C[i]
                    continue
                pnl = pos["qty"] * (ex - avg) * d - (pos["cost"] + pos["qty"] * ex) * FEE / 200
                equity += pnl; peak = max(peak, equity); dd = max(dd, (peak - equity) / peak * 100)
                trades.append(dict(t=c[i]["timestamp"], pnl=pnl / (equity - pnl) * 100))
                pos = None
                continue
            d = 1 if C[i] < lo else -1 if C[i] > up else 0
            if d:
                leg = equity * notional_pct / 100; q = leg / C[i]
                pos = dict(i=i, d=d, qty=q, cost=q * C[i], legs=1, last=C[i], atr=a, leg=leg)
        summarize(name, trades, equity, dd, n_days, worst_expo)

    tiny("A1  target 25% to SMA20, stop 3xATR, 48h, 1% risk", 0.25, 3.0, 48)
    tiny("A2  target 25% to SMA20, stop 5xATR, 96h, 1% risk", 0.25, 5.0, 96)
    tiny("A3  target 10% to SMA20, NO stop, 240h, 20% notional", 0.10, 0.0, 240)
    grid("B1  grid: add 10% notional every 1xATR against, up to 4 legs, TP +0.3% from avg, 240h", 3, 1.0, 0.3, 240, 10.0)
    grid("B2  grid: add every 1.5xATR, up to 6 legs, TP +0.5%, 480h", 5, 1.5, 0.5, 480, 10.0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", default="ETH-USD")
    p.add_argument("--days", type=int, default=3400)
    a = p.parse_args()
    run(a.pair, a.days)


if __name__ == "__main__":
    main()
