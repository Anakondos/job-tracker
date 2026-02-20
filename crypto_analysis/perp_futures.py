"""
ETH Perpetual Futures Analysis Module

Data sources (all public, no auth required):
- Coinbase Exchange API: ETH-USD spot candles (OHLCV)
- Coinbase INTX API: ETH-PERP instrument data, funding rates, quotes

Usage:
    python -m crypto_analysis.perp_futures
    python -m crypto_analysis.perp_futures --hours 24
    python -m crypto_analysis.perp_futures --pair SOL --hours 48 --granularity 5m
"""

import argparse
import json
import os
import time
import requests
from datetime import datetime, timedelta
from typing import Optional

from crypto_analysis.technical_analysis import (
    compute_all_indicators,
    generate_summary,
)


COINBASE_EXCHANGE = "https://api.exchange.coinbase.com"
COINBASE_INTX = "https://api.international.coinbase.com/api/v1"

GRANULARITY_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}

PERP_INSTRUMENTS = {
    "BTC": {"spot": "BTC-USD", "perp": "BTC-PERP"},
    "ETH": {"spot": "ETH-USD", "perp": "ETH-PERP"},
    "SOL": {"spot": "SOL-USD", "perp": "SOL-PERP"},
    "AVAX": {"spot": "AVAX-USD", "perp": "AVAX-PERP"},
    "DOGE": {"spot": "DOGE-USD", "perp": "DOGE-PERP"},
    "LINK": {"spot": "LINK-USD", "perp": "LINK-PERP"},
    "XRP": {"spot": "XRP-USD", "perp": "XRP-PERP"},
}


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def get_spot_candles(
    pair: str, granularity_sec: int, hours_back: int
) -> list[dict]:
    """Fetch OHLCV candles from Coinbase Exchange (spot, public)."""
    max_per_request = 300
    seconds_per_request = max_per_request * granularity_sec

    end = datetime.utcnow()
    start = end - timedelta(hours=hours_back)
    all_candles = []
    current = start

    while current < end:
        chunk_end = min(current + timedelta(seconds=seconds_per_request), end)
        params = {
            "start": current.isoformat() + "Z",
            "end": chunk_end.isoformat() + "Z",
            "granularity": granularity_sec,
        }
        resp = requests.get(
            f"{COINBASE_EXCHANGE}/products/{pair}/candles",
            params=params, timeout=15,
        )
        resp.raise_for_status()
        for ts, low, high, open_, close, volume in resp.json():
            all_candles.append({
                "timestamp": datetime.utcfromtimestamp(ts),
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            })
        current = chunk_end
        time.sleep(0.15)

    all_candles.sort(key=lambda c: c["timestamp"])
    return all_candles


def get_perp_instrument(symbol: str) -> dict:
    """Get live instrument data for a PERP from INTX."""
    url = f"{COINBASE_INTX}/instruments/{symbol}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_funding_history(symbol: str, limit: int = 100) -> list[dict]:
    """Get hourly funding rate history from INTX."""
    url = f"{COINBASE_INTX}/instruments/{symbol}/funding"
    all_results = []
    offset = 0

    while len(all_results) < limit:
        batch = min(100, limit - len(all_results))
        params = {"result_limit": batch, "result_offset": offset}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break
        all_results.extend(results)
        offset += len(results)
        time.sleep(0.1)

    return all_results


def get_all_perp_instruments() -> list[dict]:
    """List all available PERP instruments on INTX."""
    resp = requests.get(f"{COINBASE_INTX}/instruments", timeout=10)
    resp.raise_for_status()
    return [i for i in resp.json() if i.get("type") == "PERP"]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_funding(funding_history: list[dict]) -> dict:
    """Analyze funding rate patterns."""
    if not funding_history:
        return {}

    rates = [float(f["funding_rate"]) for f in funding_history]
    marks = [float(f["mark_price"]) for f in funding_history]

    avg_rate = sum(rates) / len(rates)
    max_rate = max(rates)
    min_rate = min(rates)
    positive_count = sum(1 for r in rates if r > 0)
    negative_count = sum(1 for r in rates if r < 0)

    # Annualized rates
    avg_annual = avg_rate * 8760 * 100
    max_annual = max_rate * 8760 * 100
    min_annual = min_rate * 8760 * 100

    # Funding trend (last 12h vs previous 12h)
    if len(rates) >= 24:
        recent = sum(rates[:12]) / 12
        previous = sum(rates[12:24]) / 12
        trend = "increasing" if recent > previous else "decreasing" if recent < previous else "flat"
    else:
        trend = "insufficient data"

    return {
        "count": len(rates),
        "avg_rate": avg_rate,
        "max_rate": max_rate,
        "min_rate": min_rate,
        "avg_annual_pct": avg_annual,
        "max_annual_pct": max_annual,
        "min_annual_pct": min_annual,
        "positive_hours": positive_count,
        "negative_hours": negative_count,
        "trend": trend,
        "latest_rate": rates[0],
        "latest_mark": marks[0],
    }


def compute_basis(spot_price: float, mark_price: float) -> dict:
    """Compute basis (perp - spot) and annualized basis rate."""
    basis = mark_price - spot_price
    basis_pct = (basis / spot_price) * 100 if spot_price else 0
    return {
        "basis_usd": basis,
        "basis_pct": basis_pct,
        "direction": "contango" if basis > 0 else "backwardation" if basis < 0 else "flat",
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_perp_report(
    asset: str,
    candles: list[dict],
    instrument: dict,
    funding_history: list[dict],
    indicators: dict,
) -> str:
    """Generate comprehensive perp futures report."""
    quote = instrument.get("quote", {})
    mark_price = float(quote.get("mark_price", 0))
    index_price = float(quote.get("index_price", 0))
    open_interest = float(instrument.get("open_interest", 0))
    spot_price = candles[-1]["close"] if candles else 0

    lines = []
    lines.append("=" * 60)
    lines.append(f"  {asset} PERPETUAL FUTURES ANALYSIS")
    lines.append(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)

    # --- Live Perp Data ---
    lines.append("\n--- Live Data (Coinbase INTX) ---")
    lines.append(f"  Mark price:    ${mark_price:,.2f}")
    lines.append(f"  Index price:   ${index_price:,.2f}")
    lines.append(f"  Spot price:    ${spot_price:,.2f}")
    bid = float(quote.get("best_bid_price", 0))
    ask = float(quote.get("best_ask_price", 0))
    lines.append(f"  Best bid/ask:  ${bid:,.2f} / ${ask:,.2f}  (spread: ${ask - bid:,.2f})")
    lines.append(f"  Open interest: {open_interest:,.2f} {asset} (${open_interest * mark_price:,.0f})")

    # --- Basis ---
    basis = compute_basis(spot_price, mark_price)
    lines.append(f"\n--- Basis ---")
    lines.append(f"  Perp - Spot:   ${basis['basis_usd']:+,.2f} ({basis['basis_pct']:+.4f}%)")
    lines.append(f"  Structure:     {basis['direction'].upper()}")

    # --- Funding Analysis ---
    fa = analyze_funding(funding_history)
    if fa:
        lines.append(f"\n--- Funding Rate Analysis ({fa['count']} hours) ---")
        lines.append(f"  Latest rate:   {fa['latest_rate']:+.6f} ({fa['latest_rate'] * 8760 * 100:+.2f}% ann.)")
        lines.append(f"  Average rate:  {fa['avg_rate']:+.6f} ({fa['avg_annual_pct']:+.2f}% ann.)")
        lines.append(f"  Max rate:      {fa['max_rate']:+.6f} ({fa['max_annual_pct']:+.2f}% ann.)")
        lines.append(f"  Min rate:      {fa['min_rate']:+.6f} ({fa['min_annual_pct']:+.2f}% ann.)")
        lines.append(f"  Positive hrs:  {fa['positive_hours']}  |  Negative hrs: {fa['negative_hours']}")
        lines.append(f"  Trend (12h):   {fa['trend']}")
        predicted = quote.get("predicted_funding", "N/A")
        lines.append(f"  Predicted:     {predicted}")

        if fa["avg_rate"] < -0.00001:
            lines.append("  Signal:        BEARISH — shorts dominate, pay funding to longs")
        elif fa["avg_rate"] > 0.00001:
            lines.append("  Signal:        BULLISH — longs dominate, pay funding to shorts")
        else:
            lines.append("  Signal:        NEUTRAL — balanced funding")

    # --- Price Action (from spot candles) ---
    if candles:
        lines.append(f"\n--- Price Action ({len(candles)} candles) ---")
        period_high = max(c["high"] for c in candles)
        period_low = min(c["low"] for c in candles)
        open_p = candles[0]["open"]
        pct_change = ((spot_price - open_p) / open_p) * 100
        total_vol = sum(c["volume"] for c in candles)

        high_candle = next(c for c in candles if c["high"] == period_high)
        low_candle = next(c for c in candles if c["low"] == period_low)

        lines.append(f"  HIGH:          ${period_high:,.2f}  at {high_candle['timestamp'].strftime('%m-%d %H:%M UTC')}")
        lines.append(f"  LOW:           ${period_low:,.2f}  at {low_candle['timestamp'].strftime('%m-%d %H:%M UTC')}")
        lines.append(f"  Range:         ${period_high - period_low:,.2f} ({((period_high - period_low) / period_low) * 100:.2f}%)")
        lines.append(f"  Change:        {pct_change:+.2f}%")
        lines.append(f"  Total volume:  {total_vol:,.2f} {asset}")

    # --- Technical Indicators ---
    if indicators:
        lines.append(f"\n--- Technical Indicators ---")
        sma20 = indicators["sma_20"][-1]
        sma50 = indicators["sma_50"][-1]
        if sma20:
            lines.append(f"  SMA(20):       ${sma20:,.2f} {'(price above)' if spot_price > sma20 else '(price below)'}")
        if sma50:
            lines.append(f"  SMA(50):       ${sma50:,.2f} {'(price above)' if spot_price > sma50 else '(price below)'}")
        if sma20 and sma50:
            cross = "GOLDEN CROSS (bullish)" if sma20 > sma50 else "DEATH CROSS (bearish)"
            lines.append(f"  SMA cross:     {cross}")

        rsi_val = indicators["rsi_14"][-1]
        if rsi_val:
            zone = "OVERBOUGHT" if rsi_val > 70 else "OVERSOLD" if rsi_val < 30 else "NEUTRAL"
            lines.append(f"  RSI(14):       {rsi_val:.1f} — {zone}")

        macd_data = indicators["macd"]
        if macd_data["macd"][-1] is not None and macd_data["signal"][-1] is not None:
            m = macd_data["macd"][-1]
            s = macd_data["signal"][-1]
            h = macd_data["histogram"][-1]
            signal = "BULLISH" if m > s else "BEARISH"
            lines.append(f"  MACD:          {m:.2f} / Signal: {s:.2f} / Hist: {h:+.2f} — {signal}")

        bb = indicators["bollinger"]
        if bb["upper"][-1]:
            width = ((bb["upper"][-1] - bb["lower"][-1]) / bb["middle"][-1]) * 100
            lines.append(f"  Bollinger:     [{bb['lower'][-1]:,.2f} — {bb['upper'][-1]:,.2f}] width={width:.1f}%")

        atr_val = indicators["atr_14"][-1]
        if atr_val:
            lines.append(f"  ATR(14):       ${atr_val:,.2f} ({(atr_val / spot_price) * 100:.2f}% volatility)")

    # --- Combined Signal ---
    lines.append(f"\n--- Combined Signal ---")
    signals = []
    if fa:
        if fa["avg_rate"] < -0.00001:
            signals.append("funding: BEARISH")
        elif fa["avg_rate"] > 0.00001:
            signals.append("funding: BULLISH")
        else:
            signals.append("funding: NEUTRAL")

    if basis["direction"] == "backwardation":
        signals.append("basis: BEARISH (backwardation)")
    elif basis["direction"] == "contango":
        signals.append("basis: BULLISH (contango)")

    if indicators:
        rsi_val = indicators["rsi_14"][-1]
        if rsi_val and rsi_val < 30:
            signals.append("RSI: OVERSOLD")
        elif rsi_val and rsi_val > 70:
            signals.append("RSI: OVERBOUGHT")

        macd_d = indicators["macd"]
        if macd_d["macd"][-1] and macd_d["signal"][-1]:
            if macd_d["macd"][-1] > macd_d["signal"][-1]:
                signals.append("MACD: BULLISH")
            else:
                signals.append("MACD: BEARISH")

    for s in signals:
        lines.append(f"  {s}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def generate_perp_charts(
    asset: str,
    candles: list[dict],
    indicators: dict,
    funding_history: list[dict],
    output_dir: str,
):
    """Generate perp-specific charts with funding rate panel."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("  [matplotlib not installed — skipping charts]")
        return

    os.makedirs(output_dir, exist_ok=True)
    timestamps = indicators["timestamps"]

    fig, axes = plt.subplots(5, 1, figsize=(16, 24), sharex=False,
                              gridspec_kw={"height_ratios": [3, 1.5, 1.5, 1.5, 1.5]})
    fig.suptitle(f"{asset}-PERP Analysis", fontsize=16, fontweight="bold")

    # Panel 1: Price + SMA + Bollinger
    ax1 = axes[0]
    ax1.plot(timestamps, indicators["closes"], label="Close", color="#2196F3", linewidth=1.5)
    if any(v is not None for v in indicators["sma_20"]):
        ax1.plot(timestamps, indicators["sma_20"], label="SMA(20)", color="#FF9800", linewidth=1, alpha=0.8)
    if any(v is not None for v in indicators["sma_50"]):
        ax1.plot(timestamps, indicators["sma_50"], label="SMA(50)", color="#E91E63", linewidth=1, alpha=0.8)
    bb = indicators["bollinger"]
    valid = [(timestamps[i], bb["upper"][i], bb["lower"][i])
             for i in range(len(timestamps)) if bb["upper"][i] is not None]
    if valid:
        ts_v, up_v, lo_v = zip(*valid)
        ax1.fill_between(ts_v, lo_v, up_v, alpha=0.1, color="#9C27B0", label="Bollinger")
    ax1.set_ylabel("Price (USD)")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Panel 2: Volume
    ax2 = axes[1]
    colors = ["#4CAF50" if i == 0 or candles[i]["close"] >= candles[i-1]["close"] else "#F44336"
              for i in range(len(candles))]
    ax2.bar(timestamps, indicators["volumes"], color=colors, alpha=0.7, width=0.02)
    ax2.set_ylabel("Volume")
    ax2.grid(True, alpha=0.3)

    # Panel 3: RSI
    ax3 = axes[2]
    rsi_vals = indicators["rsi_14"]
    valid_rsi = [(timestamps[i], rsi_vals[i]) for i in range(len(timestamps)) if rsi_vals[i] is not None]
    if valid_rsi:
        ts_r, r_v = zip(*valid_rsi)
        ax3.plot(ts_r, r_v, color="#673AB7", linewidth=1.2)
        ax3.axhline(y=70, color="#F44336", linestyle="--", alpha=0.5)
        ax3.axhline(y=30, color="#4CAF50", linestyle="--", alpha=0.5)
    ax3.set_ylabel("RSI(14)")
    ax3.set_ylim(0, 100)
    ax3.grid(True, alpha=0.3)

    # Panel 4: MACD
    ax4 = axes[3]
    macd_d = indicators["macd"]
    valid_m = [(timestamps[i], macd_d["macd"][i]) for i in range(len(timestamps)) if macd_d["macd"][i] is not None]
    valid_s = [(timestamps[i], macd_d["signal"][i]) for i in range(len(timestamps)) if macd_d["signal"][i] is not None]
    if valid_m:
        ts_m, m_v = zip(*valid_m)
        ax4.plot(ts_m, m_v, label="MACD", color="#2196F3", linewidth=1.2)
    if valid_s:
        ts_s, s_v = zip(*valid_s)
        ax4.plot(ts_s, s_v, label="Signal", color="#FF9800", linewidth=1)
    hist_vals = [(timestamps[i], macd_d["histogram"][i]) for i in range(len(timestamps)) if macd_d["histogram"][i] is not None]
    if hist_vals:
        ts_h, h_v = zip(*hist_vals)
        h_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in h_v]
        ax4.bar(ts_h, h_v, color=h_colors, alpha=0.5, width=0.02)
    ax4.set_ylabel("MACD")
    ax4.legend(loc="upper left", fontsize=8)
    ax4.grid(True, alpha=0.3)

    # Panel 5: Funding Rate
    ax5 = axes[4]
    if funding_history:
        fr_times = []
        fr_rates = []
        for f in reversed(funding_history):
            try:
                t = datetime.strptime(f["event_time"], "%Y-%m-%dT%H:%M:%SZ")
                fr_times.append(t)
                fr_rates.append(float(f["funding_rate"]) * 100)  # to percent
            except (ValueError, KeyError):
                continue
        if fr_times:
            fr_colors = ["#4CAF50" if r >= 0 else "#F44336" for r in fr_rates]
            ax5.bar(fr_times, fr_rates, color=fr_colors, alpha=0.7, width=0.03)
            ax5.axhline(y=0, color="gray", linewidth=0.5)
            ax5.set_ylabel("Funding Rate (%)")
            ax5.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
            ax5.grid(True, alpha=0.3)
    ax5.set_xlabel("Time (UTC)")

    for ax in axes[:4]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))

    plt.tight_layout()
    path = os.path.join(output_dir, f"{asset}_PERP_analysis.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Chart saved: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(asset: str, hours_back: int, granularity: str, output_dir: str):
    """Run full perp futures analysis."""
    info = PERP_INSTRUMENTS.get(asset.upper())
    if not info:
        print(f"Unknown asset: {asset}. Available: {', '.join(PERP_INSTRUMENTS.keys())}")
        return

    spot_pair = info["spot"]
    perp_symbol = info["perp"]
    gran_sec = GRANULARITY_MAP.get(granularity, 3600)
    funding_hours = max(hours_back, 48)  # at least 48h of funding data

    print(f"\nFetching {asset} data...")

    # 1. Spot candles
    print(f"  Spot candles ({spot_pair}, {granularity}, {hours_back}h)...")
    candles = get_spot_candles(spot_pair, gran_sec, hours_back)
    print(f"    -> {len(candles)} candles")

    # 2. INTX instrument (live quote)
    print(f"  INTX instrument ({perp_symbol})...")
    try:
        instrument = get_perp_instrument(perp_symbol)
        print(f"    -> mark=${float(instrument['quote']['mark_price']):,.2f}")
    except Exception as e:
        print(f"    -> Error: {e}")
        instrument = {"quote": {}, "open_interest": "0"}

    # 3. Funding history
    print(f"  Funding history ({funding_hours}h)...")
    try:
        funding = get_funding_history(perp_symbol, limit=funding_hours)
        print(f"    -> {len(funding)} entries")
    except Exception as e:
        print(f"    -> Error: {e}")
        funding = []

    # 4. Compute indicators
    indicators = compute_all_indicators(candles) if candles else {}

    # 5. Generate report
    report = generate_perp_report(asset, candles, instrument, funding, indicators)
    print(report)

    # 6. Charts
    print("\n--- Generating Charts ---")
    generate_perp_charts(asset, candles, indicators, funding, output_dir)

    # 7. Save data
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"{asset}_perp_data.json")
    export = {
        "asset": asset,
        "timestamp": datetime.utcnow().isoformat(),
        "spot_price": candles[-1]["close"] if candles else None,
        "mark_price": float(instrument.get("quote", {}).get("mark_price", 0)),
        "index_price": float(instrument.get("quote", {}).get("index_price", 0)),
        "open_interest": float(instrument.get("open_interest", 0)),
        "funding_rate": float(funding[0]["funding_rate"]) if funding else None,
        "funding_annual_pct": float(funding[0]["funding_rate"]) * 8760 * 100 if funding else None,
        "candle_count": len(candles),
        "funding_entries": len(funding),
    }
    with open(json_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  Data saved: {json_path}")


def main():
    parser = argparse.ArgumentParser(description="ETH Perpetual Futures Analysis")
    parser.add_argument("--pair", default="ETH", choices=list(PERP_INSTRUMENTS.keys()),
                        help="Asset to analyze (default: ETH)")
    parser.add_argument("--hours", type=int, default=24, help="Hours of price history")
    parser.add_argument("--granularity", default="5m",
                        choices=list(GRANULARITY_MAP.keys()), help="Candle granularity")
    parser.add_argument("--output", default="crypto_analysis/output",
                        help="Output directory")
    args = parser.parse_args()

    run(args.pair, args.hours, args.granularity, args.output)


if __name__ == "__main__":
    main()
