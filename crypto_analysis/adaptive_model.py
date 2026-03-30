"""
Adaptive Price Action Model

Self-learning backtesting system that:
1. Extracts patterns from historical candles (via pattern_detector)
2. Measures forward returns for each pattern
3. Builds a scoring table: pattern_key -> {win_rate, avg_return, count}
4. Adapts over time with exponential decay (recent data weighs more)
5. Generates actionable signals based on learned pattern performance

The model is instrument-specific — each asset learns its own pattern scores.

Usage:
    python -m crypto_analysis.adaptive_model --pair ETH --days 30 --granularity 1h
    python -m crypto_analysis.adaptive_model --pair BTC --days 90 --granularity 1h --forward 12
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

from crypto_analysis.data_fetcher import get_candles
from crypto_analysis.technical_analysis import compute_all_indicators
from crypto_analysis.pattern_detector import extract_patterns, PatternSnapshot


# ---------------------------------------------------------------------------
# Pattern score record
# ---------------------------------------------------------------------------

@dataclass
class PatternScore:
    """Accumulated statistics for a single pattern key."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_return: float = 0.0       # sum of all forward returns (%)
    total_return_sq: float = 0.0    # sum of squared returns (for std dev)
    best_return: float = -999.0
    worst_return: float = 999.0
    weighted_return: float = 0.0    # EMA-weighted return (recency bias)
    last_seen: str = ""

    @property
    def win_rate(self) -> float:
        return self.wins / self.total_trades if self.total_trades else 0.0

    @property
    def avg_return(self) -> float:
        return self.total_return / self.total_trades if self.total_trades else 0.0

    @property
    def return_std(self) -> float:
        if self.total_trades < 2:
            return 0.0
        variance = (self.total_return_sq / self.total_trades) - (self.avg_return ** 2)
        return math.sqrt(max(0, variance))

    @property
    def sharpe(self) -> float:
        """Simplified Sharpe-like ratio (avg_return / std)."""
        if self.return_std == 0:
            return 0.0
        return self.avg_return / self.return_std

    @property
    def expectancy(self) -> float:
        """Expected return per trade (Kelly-adjacent metric)."""
        if self.total_trades < 5:
            return 0.0  # not enough data
        avg_win = self.total_return / self.wins if self.wins else 0
        avg_loss = (self.total_return - (avg_win * self.wins)) / self.losses if self.losses else 0
        return (self.win_rate * avg_win) + ((1 - self.win_rate) * avg_loss)

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "avg_return": round(self.avg_return, 4),
            "return_std": round(self.return_std, 4),
            "sharpe": round(self.sharpe, 4),
            "best_return": round(self.best_return, 4),
            "worst_return": round(self.worst_return, 4),
            "weighted_return": round(self.weighted_return, 4),
            "expectancy": round(self.expectancy, 4),
            "last_seen": self.last_seen,
        }


# ---------------------------------------------------------------------------
# Adaptive Model
# ---------------------------------------------------------------------------

class AdaptiveModel:
    """Self-learning pattern scoring model for a specific instrument.

    The model maintains a scoring table mapping pattern keys to their
    historical performance. It uses exponential weighting so that
    recent pattern occurrences matter more than old ones.
    """

    def __init__(
        self,
        asset: str,
        forward_candles: int = 6,
        decay_factor: float = 0.95,
        min_trades: int = 5,
    ):
        self.asset = asset
        self.forward_candles = forward_candles
        self.decay_factor = decay_factor  # EMA weight: 0.95 = slow decay
        self.min_trades = min_trades
        self.scores: dict[str, PatternScore] = defaultdict(PatternScore)
        self.train_count = 0
        self.last_trained: Optional[str] = None

    # ----- Training (backtest) -----

    def train(self, candles: list[dict], indicators: dict,
              funding_rate: Optional[float] = None,
              funding_trend: Optional[str] = None):
        """Train model on historical candles by measuring forward returns."""
        patterns = extract_patterns(candles, indicators, funding_rate, funding_trend)

        if not patterns:
            print("  No patterns extracted (need 50+ candles)")
            return

        # We need forward_candles of headroom after each pattern
        # Map pattern timestamps to candle indices for forward return lookup
        ts_to_idx = {}
        for i, c in enumerate(candles):
            ts_to_idx[c["timestamp"]] = i

        trades_added = 0
        for pat in patterns:
            idx = ts_to_idx.get(pat.timestamp)
            if idx is None:
                continue
            future_idx = idx + self.forward_candles
            if future_idx >= len(candles):
                continue  # not enough forward data

            entry_price = pat.price
            exit_price = candles[future_idx]["close"]
            forward_return = ((exit_price - entry_price) / entry_price) * 100

            key = pat.to_feature_key()
            self._update_score(key, forward_return, pat.timestamp)
            trades_added += 1

        self.train_count += trades_added
        self.last_trained = datetime.utcnow().isoformat()
        print(f"  Trained on {trades_added} pattern occurrences ({len(self.scores)} unique patterns)")

    def _update_score(self, key: str, forward_return: float, timestamp):
        """Update scoring for a pattern key with a new observation."""
        s = self.scores[key]
        s.total_trades += 1
        s.total_return += forward_return
        s.total_return_sq += forward_return ** 2
        s.best_return = max(s.best_return, forward_return)
        s.worst_return = min(s.worst_return, forward_return)

        if forward_return > 0:
            s.wins += 1
        else:
            s.losses += 1

        # Exponentially weighted moving average of returns
        if s.total_trades == 1:
            s.weighted_return = forward_return
        else:
            s.weighted_return = (self.decay_factor * s.weighted_return +
                                 (1 - self.decay_factor) * forward_return)

        s.last_seen = str(timestamp)

    # ----- Prediction -----

    def predict(self, pattern: PatternSnapshot) -> dict:
        """Get prediction for a pattern based on learned scores.

        Returns dict with signal, confidence, and supporting stats.
        """
        key = pattern.to_feature_key()
        s = self.scores.get(key)

        if s is None or s.total_trades < self.min_trades:
            return {
                "signal": "NO_DATA",
                "pattern_key": key,
                "confidence": 0.0,
                "message": f"Pattern seen {s.total_trades if s else 0} times (min: {self.min_trades})",
            }

        # Combine weighted return (recency) with overall stats
        signal_strength = s.weighted_return

        if signal_strength > 0.1 and s.win_rate > 0.55:
            signal = "LONG"
        elif signal_strength < -0.1 and s.win_rate < 0.45:
            signal = "SHORT"
        else:
            signal = "NEUTRAL"

        # Confidence: based on sample size and consistency
        sample_conf = min(1.0, s.total_trades / 50)  # maxes at 50 trades
        consistency = s.win_rate if signal == "LONG" else (1 - s.win_rate) if signal == "SHORT" else 0.5
        confidence = sample_conf * consistency

        return {
            "signal": signal,
            "pattern_key": key,
            "confidence": round(confidence, 3),
            "win_rate": round(s.win_rate, 4),
            "avg_return": round(s.avg_return, 4),
            "weighted_return": round(s.weighted_return, 4),
            "sharpe": round(s.sharpe, 4),
            "total_trades": s.total_trades,
            "best": round(s.best_return, 4),
            "worst": round(s.worst_return, 4),
        }

    def get_top_patterns(self, n: int = 10, direction: str = "long") -> list[dict]:
        """Get top N performing patterns.

        Args:
            n: number of patterns to return
            direction: 'long' (best positive returns) or 'short' (best negative returns)
        """
        qualified = [
            (key, score) for key, score in self.scores.items()
            if score.total_trades >= self.min_trades
        ]

        if direction == "long":
            qualified.sort(key=lambda x: x[1].weighted_return, reverse=True)
        else:
            qualified.sort(key=lambda x: x[1].weighted_return)

        results = []
        for key, score in qualified[:n]:
            results.append({
                "pattern": key,
                **score.to_dict(),
            })
        return results

    # ----- Walk-forward validation -----

    def walk_forward_test(
        self,
        candles: list[dict],
        indicators: dict,
        train_ratio: float = 0.7,
        funding_rate: Optional[float] = None,
        funding_trend: Optional[str] = None,
    ) -> dict:
        """Split data into train/test, train on first part, evaluate on second.

        This prevents overfitting — the model only sees training data
        during learning, then we measure real performance on unseen data.
        """
        split_idx = int(len(candles) * train_ratio)
        train_candles = candles[:split_idx]
        test_candles = candles[split_idx:]

        # Need to recompute indicators for each split
        from crypto_analysis.technical_analysis import compute_all_indicators

        train_indicators = compute_all_indicators(train_candles)
        test_indicators = compute_all_indicators(test_candles)

        # Train
        fresh_model = AdaptiveModel(
            self.asset,
            forward_candles=self.forward_candles,
            decay_factor=self.decay_factor,
            min_trades=self.min_trades,
        )
        fresh_model.train(train_candles, train_indicators, funding_rate, funding_trend)

        # Test
        test_patterns = extract_patterns(test_candles, test_indicators, funding_rate, funding_trend)

        ts_to_idx = {}
        for i, c in enumerate(test_candles):
            ts_to_idx[c["timestamp"]] = i

        results = {"total": 0, "predicted": 0, "correct": 0,
                    "long_trades": 0, "short_trades": 0,
                    "long_correct": 0, "short_correct": 0,
                    "total_pnl": 0.0, "trades": []}

        for pat in test_patterns:
            idx = ts_to_idx.get(pat.timestamp)
            if idx is None:
                continue
            future_idx = idx + self.forward_candles
            if future_idx >= len(test_candles):
                continue

            actual_return = ((test_candles[future_idx]["close"] - pat.price) / pat.price) * 100
            pred = fresh_model.predict(pat)
            results["total"] += 1

            if pred["signal"] in ("LONG", "SHORT"):
                results["predicted"] += 1
                is_correct = (
                    (pred["signal"] == "LONG" and actual_return > 0) or
                    (pred["signal"] == "SHORT" and actual_return < 0)
                )
                if is_correct:
                    results["correct"] += 1

                if pred["signal"] == "LONG":
                    results["long_trades"] += 1
                    results["total_pnl"] += actual_return
                    if is_correct:
                        results["long_correct"] += 1
                else:
                    results["short_trades"] += 1
                    results["total_pnl"] -= actual_return  # short profits from drops
                    if is_correct:
                        results["short_correct"] += 1

                results["trades"].append({
                    "time": str(pat.timestamp),
                    "signal": pred["signal"],
                    "confidence": pred["confidence"],
                    "actual_return": round(actual_return, 4),
                    "correct": is_correct,
                })

        if results["predicted"]:
            results["accuracy"] = round(results["correct"] / results["predicted"], 4)
            results["avg_pnl"] = round(results["total_pnl"] / results["predicted"], 4)
        else:
            results["accuracy"] = 0
            results["avg_pnl"] = 0

        results["total_pnl"] = round(results["total_pnl"], 4)
        results["patterns_learned"] = len(fresh_model.scores)
        results["train_size"] = len(train_candles)
        results["test_size"] = len(test_candles)

        # Don't include individual trades in summary
        trade_details = results.pop("trades")

        return results, trade_details, fresh_model

    # ----- Incremental learning -----

    def incremental_update(
        self,
        new_candles: list[dict],
        indicators: dict,
        funding_rate: Optional[float] = None,
        funding_trend: Optional[str] = None,
    ):
        """Update model with new data without full retrain.

        Call this periodically (e.g. every hour) to keep the model
        adapting to changing market conditions. The exponential decay
        ensures recent patterns gradually outweigh old ones.
        """
        self.train(new_candles, indicators, funding_rate, funding_trend)

    # ----- Persistence -----

    def save(self, path: str):
        """Save model state to JSON."""
        data = {
            "asset": self.asset,
            "forward_candles": self.forward_candles,
            "decay_factor": self.decay_factor,
            "min_trades": self.min_trades,
            "train_count": self.train_count,
            "last_trained": self.last_trained,
            "scores": {
                key: asdict(score) for key, score in self.scores.items()
            },
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Model saved: {path} ({len(self.scores)} patterns)")

    @classmethod
    def load(cls, path: str) -> "AdaptiveModel":
        """Load model state from JSON."""
        with open(path) as f:
            data = json.load(f)

        model = cls(
            asset=data["asset"],
            forward_candles=data["forward_candles"],
            decay_factor=data["decay_factor"],
            min_trades=data["min_trades"],
        )
        model.train_count = data["train_count"]
        model.last_trained = data["last_trained"]

        for key, score_dict in data["scores"].items():
            ps = PatternScore(**score_dict)
            model.scores[key] = ps

        print(f"  Model loaded: {path} ({len(model.scores)} patterns, {model.train_count} observations)")
        return model


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def generate_model_report(
    model: AdaptiveModel,
    wf_results: Optional[dict] = None,
) -> str:
    """Generate human-readable model performance report."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  ADAPTIVE MODEL REPORT: {model.asset}")
    lines.append(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("=" * 60)

    lines.append(f"\n--- Model Config ---")
    lines.append(f"  Forward candles: {model.forward_candles}")
    lines.append(f"  Decay factor:    {model.decay_factor}")
    lines.append(f"  Min trades:      {model.min_trades}")
    lines.append(f"  Total patterns:  {len(model.scores)}")
    lines.append(f"  Total observations: {model.train_count}")
    lines.append(f"  Last trained:    {model.last_trained}")

    # Top long patterns
    top_long = model.get_top_patterns(10, "long")
    if top_long:
        lines.append(f"\n--- Top 10 LONG Patterns ---")
        lines.append(f"  {'Pattern':<50} {'WR':>6} {'AvgR':>8} {'WtdR':>8} {'#':>5}")
        lines.append(f"  {'-'*50} {'---':>6} {'----':>8} {'----':>8} {'-':>5}")
        for p in top_long:
            lines.append(
                f"  {p['pattern']:<50} {p['win_rate']:>6.1%} {p['avg_return']:>+7.3f}% "
                f"{p['weighted_return']:>+7.3f}% {p['total_trades']:>5}"
            )

    # Top short patterns
    top_short = model.get_top_patterns(10, "short")
    if top_short:
        lines.append(f"\n--- Top 10 SHORT Patterns ---")
        lines.append(f"  {'Pattern':<50} {'WR':>6} {'AvgR':>8} {'WtdR':>8} {'#':>5}")
        lines.append(f"  {'-'*50} {'---':>6} {'----':>8} {'----':>8} {'-':>5}")
        for p in top_short:
            lines.append(
                f"  {p['pattern']:<50} {p['win_rate']:>6.1%} {p['avg_return']:>+7.3f}% "
                f"{p['weighted_return']:>+7.3f}% {p['total_trades']:>5}"
            )

    # Walk-forward results
    if wf_results:
        lines.append(f"\n--- Walk-Forward Validation ---")
        lines.append(f"  Train size:      {wf_results['train_size']} candles")
        lines.append(f"  Test size:       {wf_results['test_size']} candles")
        lines.append(f"  Total patterns:  {wf_results['total']}")
        lines.append(f"  Signals given:   {wf_results['predicted']}")
        lines.append(f"  Accuracy:        {wf_results['accuracy']:.1%}")
        lines.append(f"  Avg PnL/trade:   {wf_results['avg_pnl']:+.4f}%")
        lines.append(f"  Total PnL:       {wf_results['total_pnl']:+.4f}%")
        lines.append(f"  Long trades:     {wf_results['long_trades']} (correct: {wf_results['long_correct']})")
        lines.append(f"  Short trades:    {wf_results['short_trades']} (correct: {wf_results['short_correct']})")
        lines.append(f"  Patterns learned:{wf_results['patterns_learned']}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Adaptive Price Action Model")
    parser.add_argument("--pair", default="ETH-USD", help="Trading pair (e.g. ETH-USD)")
    parser.add_argument("--days", type=int, default=30, help="Days of history for training")
    parser.add_argument("--granularity", default="1h", help="Candle granularity")
    parser.add_argument("--forward", type=int, default=6, help="Forward candles for return measurement")
    parser.add_argument("--decay", type=float, default=0.95, help="EMA decay factor")
    parser.add_argument("--min-trades", type=int, default=5, help="Min observations per pattern")
    parser.add_argument("--output", default="crypto_analysis/output", help="Output directory")
    parser.add_argument("--load-model", default=None, help="Path to existing model JSON to continue training")
    parser.add_argument("--walk-forward", action="store_true", help="Run walk-forward validation")
    args = parser.parse_args()

    asset = args.pair.replace("-USD", "").replace("-", "")
    print(f"\n{'='*60}")
    print(f"  Adaptive Price Action Model — {args.pair}")
    print(f"  {args.days} days, {args.granularity} candles, forward={args.forward}")
    print(f"{'='*60}")

    # 1. Fetch data
    print(f"\n1. Fetching {args.pair} candles...")
    candles = get_candles(args.pair, args.granularity, args.days)
    print(f"   -> {len(candles)} candles")

    if len(candles) < 60:
        print("   Not enough candles for analysis (need 60+)")
        return

    # 2. Compute indicators
    print("2. Computing technical indicators...")
    indicators = compute_all_indicators(candles)

    # 3. Load or create model
    if args.load_model and os.path.exists(args.load_model):
        print(f"3. Loading existing model from {args.load_model}...")
        model = AdaptiveModel.load(args.load_model)
    else:
        print("3. Creating new model...")
        model = AdaptiveModel(
            asset=asset,
            forward_candles=args.forward,
            decay_factor=args.decay,
            min_trades=args.min_trades,
        )

    # 4. Train
    print("4. Training on historical patterns...")
    model.train(candles, indicators)

    # 5. Walk-forward validation (optional)
    wf_results = None
    if args.walk_forward:
        print("5. Running walk-forward validation (70/30 split)...")
        wf_results, trade_details, _ = model.walk_forward_test(candles, indicators)

    # 6. Current signal
    print(f"\n{'6' if args.walk_forward else '5'}. Current pattern analysis...")
    patterns = extract_patterns(candles, indicators)
    if patterns:
        latest = patterns[-1]
        prediction = model.predict(latest)
        print(f"   Pattern:    {prediction['pattern_key']}")
        print(f"   Signal:     {prediction['signal']}")
        print(f"   Confidence: {prediction['confidence']:.1%}")
        if prediction['signal'] != 'NO_DATA':
            print(f"   Win rate:   {prediction['win_rate']:.1%}")
            print(f"   Avg return: {prediction['avg_return']:+.3f}%")
            print(f"   Trades:     {prediction['total_trades']}")

    # 7. Report
    report = generate_model_report(model, wf_results)
    print(report)

    # 8. Save model & results
    os.makedirs(args.output, exist_ok=True)
    model_path = os.path.join(args.output, f"{asset}_adaptive_model.json")
    model.save(model_path)

    if wf_results:
        wf_path = os.path.join(args.output, f"{asset}_walkforward_results.json")
        with open(wf_path, "w") as f:
            json.dump(wf_results, f, indent=2)
        print(f"  Walk-forward results: {wf_path}")


if __name__ == "__main__":
    main()
