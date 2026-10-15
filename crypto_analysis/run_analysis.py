#!/usr/bin/env python3
"""
Coinbase Crypto Data Analysis
Fetches public market data and runs full technical analysis with text + chart output.

Usage:
    python -m crypto_analysis.run_analysis
    python -m crypto_analysis.run_analysis --pairs BTC-USD ETH-USD --days 60 --granularity 1d
"""

import argparse
import json
import os
from datetime import datetime

from crypto_analysis.data_fetcher import (
    get_spot_price,
    get_multi_pair_candles,
    DEFAULT_PAIRS,
)
from crypto_analysis.technical_analysis import (
    compute_all_indicators,
    generate_summary,
    correlation,
)


def try_matplotlib_charts(all_data: dict, output_dir: str):
    """Generate charts if matplotlib is available."""
    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("\n[matplotlib not installed — skipping charts. Install with: pip install matplotlib]")
        return

    os.makedirs(output_dir, exist_ok=True)

    for pair, (candles, indicators) in all_data.items():
        if not candles:
            continue

        fig, axes = plt.subplots(4, 1, figsize=(16, 20), sharex=True)
        fig.suptitle(f"{pair} Technical Analysis", fontsize=16, fontweight="bold")
        timestamps = indicators["timestamps"]

        # --- Panel 1: Price + SMA + Bollinger ---
        ax1 = axes[0]
        ax1.plot(timestamps, indicators["closes"], label="Close", color="#2196F3", linewidth=1.5)
        if any(v is not None for v in indicators["sma_20"]):
            ax1.plot(timestamps, indicators["sma_20"], label="SMA(20)", color="#FF9800", linewidth=1, alpha=0.8)
        if any(v is not None for v in indicators["sma_50"]):
            ax1.plot(timestamps, indicators["sma_50"], label="SMA(50)", color="#E91E63", linewidth=1, alpha=0.8)

        bb = indicators["bollinger"]
        upper_vals = bb["upper"]
        lower_vals = bb["lower"]
        valid_ts = [timestamps[i] for i in range(len(timestamps)) if upper_vals[i] is not None]
        valid_upper = [v for v in upper_vals if v is not None]
        valid_lower = [v for v in lower_vals if v is not None]
        if valid_ts:
            ax1.fill_between(valid_ts, valid_lower, valid_upper, alpha=0.1, color="#9C27B0", label="Bollinger")

        ax1.set_ylabel("Price (USD)")
        ax1.legend(loc="upper left", fontsize=8)
        ax1.grid(True, alpha=0.3)

        # --- Panel 2: Volume ---
        ax2 = axes[1]
        colors = []
        for i, c in enumerate(candles):
            if i == 0:
                colors.append("#4CAF50")
            else:
                colors.append("#4CAF50" if c["close"] >= candles[i - 1]["close"] else "#F44336")
        ax2.bar(timestamps, indicators["volumes"], color=colors, alpha=0.7, width=0.02)
        ax2.set_ylabel("Volume")
        ax2.grid(True, alpha=0.3)

        # --- Panel 3: RSI ---
        ax3 = axes[2]
        rsi_vals = indicators["rsi_14"]
        valid_rsi_ts = [timestamps[i] for i in range(len(timestamps)) if rsi_vals[i] is not None]
        valid_rsi = [v for v in rsi_vals if v is not None]
        ax3.plot(valid_rsi_ts, valid_rsi, color="#673AB7", linewidth=1.2)
        ax3.axhline(y=70, color="#F44336", linestyle="--", alpha=0.5, label="Overbought (70)")
        ax3.axhline(y=30, color="#4CAF50", linestyle="--", alpha=0.5, label="Oversold (30)")
        ax3.fill_between(valid_rsi_ts, 70, [max(v, 70) for v in valid_rsi], alpha=0.2, color="#F44336")
        ax3.fill_between(valid_rsi_ts, 30, [min(v, 30) for v in valid_rsi], alpha=0.2, color="#4CAF50")
        ax3.set_ylabel("RSI(14)")
        ax3.set_ylim(0, 100)
        ax3.legend(loc="upper left", fontsize=8)
        ax3.grid(True, alpha=0.3)

        # --- Panel 4: MACD ---
        ax4 = axes[3]
        macd_data = indicators["macd"]
        macd_line = macd_data["macd"]
        signal_line = macd_data["signal"]
        hist_vals = macd_data["histogram"]

        valid_macd_ts = [timestamps[i] for i in range(len(timestamps)) if macd_line[i] is not None]
        valid_macd = [v for v in macd_line if v is not None]
        valid_signal_ts = [timestamps[i] for i in range(len(timestamps)) if signal_line[i] is not None]
        valid_signal = [v for v in signal_line if v is not None]

        ax4.plot(valid_macd_ts, valid_macd, label="MACD", color="#2196F3", linewidth=1.2)
        ax4.plot(valid_signal_ts, valid_signal, label="Signal", color="#FF9800", linewidth=1)

        hist_ts = [timestamps[i] for i in range(len(timestamps)) if hist_vals[i] is not None]
        hist_v = [v for v in hist_vals if v is not None]
        hist_colors = ["#4CAF50" if v >= 0 else "#F44336" for v in hist_v]
        ax4.bar(hist_ts, hist_v, color=hist_colors, alpha=0.5, width=0.02)

        ax4.set_ylabel("MACD")
        ax4.legend(loc="upper left", fontsize=8)
        ax4.grid(True, alpha=0.3)

        ax4.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        ax4.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.xticks(rotation=45)

        plt.tight_layout()
        chart_path = os.path.join(output_dir, f"{pair.replace('-', '_')}_analysis.png")
        plt.savefig(chart_path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"  Chart saved: {chart_path}")


def run(pairs: list[str], granularity: str, days_back: int, output_dir: str):
    """Main analysis pipeline."""
    print("=" * 60)
    print("  COINBASE CRYPTO DATA ANALYSIS")
    print(f"  Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pairs: {', '.join(pairs)}")
    print(f"  Granularity: {granularity}  |  Days back: {days_back}")
    print("=" * 60)

    # 1. Current spot prices
    print("\n--- Current Spot Prices ---")
    for pair in pairs:
        try:
            info = get_spot_price(pair)
            print(f"  {info['pair']}: ${info['price']:,.2f}")
        except Exception as e:
            print(f"  {pair}: Error — {e}")

    # 2. Fetch candle data
    print("\n--- Fetching Historical Data ---")
    candle_data = get_multi_pair_candles(pairs, granularity, days_back)

    # 3. Compute indicators & summaries
    all_data = {}
    print("\n--- Technical Analysis ---")
    for pair in pairs:
        candles = candle_data.get(pair, [])
        if not candles:
            print(f"\n{pair}: No data")
            continue
        indicators = compute_all_indicators(candles)
        summary = generate_summary(pair, candles, indicators)
        print(f"\n{summary}")
        all_data[pair] = (candles, indicators)

    # 4. Cross-pair correlations
    if len(all_data) >= 2:
        print("\n--- Cross-Pair Correlations (returns) ---")
        pair_names = list(all_data.keys())
        for i in range(len(pair_names)):
            for j in range(i + 1, len(pair_names)):
                pa, pb = pair_names[i], pair_names[j]
                closes_a = [c["close"] for c in all_data[pa][0]]
                closes_b = [c["close"] for c in all_data[pb][0]]
                corr = correlation(closes_a, closes_b)
                if corr is not None:
                    strength = "strong" if abs(corr) > 0.7 else "moderate" if abs(corr) > 0.4 else "weak"
                    print(f"  {pa} <-> {pb}: {corr:.4f} ({strength})")

    # 5. Charts
    print("\n--- Generating Charts ---")
    try_matplotlib_charts(all_data, output_dir)

    # 6. Save raw data as JSON
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "analysis_data.json")
    export = {}
    for pair, (candles, indicators) in all_data.items():
        export[pair] = {
            "candle_count": len(candles),
            "first_candle": candles[0]["timestamp"].isoformat() if candles else None,
            "last_candle": candles[-1]["timestamp"].isoformat() if candles else None,
            "current_price": candles[-1]["close"] if candles else None,
            "sma_20": indicators["sma_20"][-1],
            "sma_50": indicators["sma_50"][-1],
            "rsi_14": indicators["rsi_14"][-1],
            "macd": indicators["macd"]["macd"][-1],
            "macd_signal": indicators["macd"]["signal"][-1],
            "atr_14": indicators["atr_14"][-1],
        }
    with open(json_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  Data saved: {json_path}")

    print("\n" + "=" * 60)
    print("  Analysis complete.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Coinbase Crypto Technical Analysis")
    parser.add_argument("--pairs", nargs="+", default=DEFAULT_PAIRS, help="Trading pairs (e.g. BTC-USD ETH-USD)")
    parser.add_argument("--granularity", default="1h", choices=["1m", "5m", "15m", "1h", "6h", "1d"], help="Candle granularity")
    parser.add_argument("--days", type=int, default=30, help="Days of historical data")
    parser.add_argument("--output", default="crypto_analysis/output", help="Output directory for charts/data")
    args = parser.parse_args()

    run(args.pairs, args.granularity, args.days, args.output)


if __name__ == "__main__":
    main()
