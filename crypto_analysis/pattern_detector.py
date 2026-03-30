"""
Price Action Pattern Detector

Extracts normalized candle patterns and market structure features
from OHLCV data. Patterns are encoded as feature vectors for use
by the adaptive scoring model.

Each pattern captures:
- Candle morphology (body/wick ratios, relative size)
- Multi-candle formations (engulfing, inside bar, etc.)
- Context: position relative to SMA, Bollinger, VWAP
- Volume profile (relative to rolling average)
- Funding rate regime (for perps)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Single-candle morphology
# ---------------------------------------------------------------------------

@dataclass
class CandleMorphology:
    """Normalized single-candle shape descriptor."""
    body_ratio: float        # |close-open| / (high-low), 0=doji 1=marubozu
    upper_wick_ratio: float  # upper_wick / (high-low)
    lower_wick_ratio: float  # lower_wick / (high-low)
    is_bullish: bool
    relative_size: float     # candle range / ATR(14) — how big vs recent avg
    volume_ratio: float      # volume / SMA(volume, 20)

    @property
    def label(self) -> str:
        if self.body_ratio < 0.1:
            return "doji"
        if self.lower_wick_ratio > 0.6 and self.body_ratio < 0.3:
            return "hammer" if self.is_bullish else "hanging_man"
        if self.upper_wick_ratio > 0.6 and self.body_ratio < 0.3:
            return "shooting_star" if not self.is_bullish else "inverted_hammer"
        if self.body_ratio > 0.8:
            return "marubozu_bull" if self.is_bullish else "marubozu_bear"
        return "bull" if self.is_bullish else "bear"


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def classify_candle(
    candle: dict,
    atr: Optional[float],
    vol_sma: Optional[float],
) -> CandleMorphology:
    """Classify a single candle into its morphology."""
    o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
    rng = h - l
    body = abs(c - o)
    is_bull = c >= o

    if is_bull:
        upper_wick = h - c
        lower_wick = o - l
    else:
        upper_wick = h - o
        lower_wick = c - l

    return CandleMorphology(
        body_ratio=_safe_ratio(body, rng),
        upper_wick_ratio=_safe_ratio(upper_wick, rng),
        lower_wick_ratio=_safe_ratio(lower_wick, rng),
        is_bullish=is_bull,
        relative_size=rng / atr if atr and atr > 0 else 1.0,
        volume_ratio=candle["volume"] / vol_sma if vol_sma and vol_sma > 0 else 1.0,
    )


# ---------------------------------------------------------------------------
# Multi-candle formations
# ---------------------------------------------------------------------------

FORMATION_NAMES = [
    "bullish_engulfing",
    "bearish_engulfing",
    "inside_bar",
    "outside_bar",
    "three_white_soldiers",
    "three_black_crows",
    "morning_star",
    "evening_star",
    "tweezer_top",
    "tweezer_bottom",
    "bull_harami",
    "bear_harami",
]


def detect_formations(candles: list[dict], idx: int) -> list[str]:
    """Detect multi-candle formations ending at candles[idx]."""
    if idx < 2:
        return []

    formations = []
    c0 = candles[idx]      # current
    c1 = candles[idx - 1]  # previous
    c2 = candles[idx - 2]  # two back

    body0 = abs(c0["close"] - c0["open"])
    body1 = abs(c1["close"] - c1["open"])
    bull0 = c0["close"] >= c0["open"]
    bull1 = c1["close"] >= c1["open"]
    bull2 = c2["close"] >= c2["open"]

    # Engulfing
    if bull0 and not bull1 and c0["open"] <= c1["close"] and c0["close"] >= c1["open"]:
        formations.append("bullish_engulfing")
    if not bull0 and bull1 and c0["open"] >= c1["close"] and c0["close"] <= c1["open"]:
        formations.append("bearish_engulfing")

    # Inside bar
    if c0["high"] <= c1["high"] and c0["low"] >= c1["low"]:
        formations.append("inside_bar")

    # Outside bar
    if c0["high"] >= c1["high"] and c0["low"] <= c1["low"]:
        formations.append("outside_bar")

    # Harami
    if bull0 and not bull1 and c0["open"] >= c1["close"] and c0["close"] <= c1["open"] and body0 < body1 * 0.5:
        formations.append("bull_harami")
    if not bull0 and bull1 and c0["open"] <= c1["close"] and c0["close"] >= c1["open"] and body0 < body1 * 0.5:
        formations.append("bear_harami")

    # Three white soldiers / three black crows
    if bull0 and bull1 and bull2:
        if c0["close"] > c1["close"] > c2["close"] and c0["open"] > c1["open"] > c2["open"]:
            formations.append("three_white_soldiers")
    if not bull0 and not bull1 and not bull2:
        if c0["close"] < c1["close"] < c2["close"] and c0["open"] < c1["open"] < c2["open"]:
            formations.append("three_black_crows")

    # Morning star / Evening star (simplified)
    body2 = abs(c2["close"] - c2["open"])
    if not bull2 and bull0 and body1 < body2 * 0.3 and body0 > body2 * 0.5:
        if c0["close"] > (c2["open"] + c2["close"]) / 2:
            formations.append("morning_star")
    if bull2 and not bull0 and body1 < body2 * 0.3 and body0 > body2 * 0.5:
        if c0["close"] < (c2["open"] + c2["close"]) / 2:
            formations.append("evening_star")

    # Tweezer (within 0.1% tolerance)
    tol = c0["high"] * 0.001
    if abs(c0["high"] - c1["high"]) < tol and bull1 and not bull0:
        formations.append("tweezer_top")
    if abs(c0["low"] - c1["low"]) < tol and not bull1 and bull0:
        formations.append("tweezer_bottom")

    return formations


# ---------------------------------------------------------------------------
# Market context features
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """Where price sits relative to key indicators."""
    price_vs_sma20: float    # (price - sma20) / sma20, e.g. +0.02 = 2% above
    price_vs_sma50: float
    bb_position: float       # 0=at lower band, 0.5=middle, 1=upper band
    rsi: Optional[float]
    macd_histogram: Optional[float]
    volume_ratio: float      # current vol / avg vol
    atr_pct: float           # ATR / price — normalized volatility
    funding_rate: Optional[float]   # for perps only
    funding_trend: Optional[str]    # "increasing" / "decreasing" / "flat"


def compute_context(
    candle: dict,
    indicators: dict,
    idx: int,
    funding_rate: Optional[float] = None,
    funding_trend: Optional[str] = None,
) -> MarketContext:
    """Compute market context for candle at position idx."""
    price = candle["close"]

    sma20 = indicators["sma_20"][idx]
    sma50 = indicators["sma_50"][idx]
    bb = indicators["bollinger"]
    rsi_val = indicators["rsi_14"][idx]
    macd_hist = indicators["macd"]["histogram"][idx]
    atr_val = indicators["atr_14"][idx]

    # Bollinger position: 0 = lower, 1 = upper
    bb_pos = 0.5
    if bb["upper"][idx] is not None and bb["lower"][idx] is not None:
        bb_range = bb["upper"][idx] - bb["lower"][idx]
        if bb_range > 0:
            bb_pos = (price - bb["lower"][idx]) / bb_range
            bb_pos = max(0.0, min(1.0, bb_pos))

    # Volume SMA (rolling 20)
    vols = indicators["volumes"]
    vol_start = max(0, idx - 19)
    vol_window = [v for v in vols[vol_start:idx + 1] if v > 0]
    vol_sma = sum(vol_window) / len(vol_window) if vol_window else 1.0
    vol_ratio = candle["volume"] / vol_sma if vol_sma > 0 else 1.0

    return MarketContext(
        price_vs_sma20=(price - sma20) / sma20 if sma20 else 0.0,
        price_vs_sma50=(price - sma50) / sma50 if sma50 else 0.0,
        bb_position=bb_pos,
        rsi=rsi_val,
        macd_histogram=macd_hist,
        volume_ratio=vol_ratio,
        atr_pct=atr_val / price if atr_val and price else 0.0,
        funding_rate=funding_rate,
        funding_trend=funding_trend,
    )


# ---------------------------------------------------------------------------
# Full pattern snapshot (one per candle)
# ---------------------------------------------------------------------------

@dataclass
class PatternSnapshot:
    """Complete pattern description at a single point in time."""
    timestamp: object  # datetime
    price: float
    morphology: CandleMorphology
    formations: list[str]
    context: MarketContext

    def to_feature_key(self) -> str:
        """Create a hashable key grouping similar patterns together.

        The key bins continuous values into discrete buckets so that
        similar market conditions map to the same key. This is what
        the adaptive scorer uses to accumulate statistics.
        """
        def _bin(val: float, step: float) -> int:
            return round(val / step)

        parts = [
            self.morphology.label,
            f"sz{_bin(self.morphology.relative_size, 0.5)}",
            f"vr{_bin(self.morphology.volume_ratio, 0.5)}",
            f"sma{_bin(self.context.price_vs_sma20, 0.01)}",
            f"bb{_bin(self.context.bb_position, 0.2)}",
        ]

        if self.context.rsi is not None:
            if self.context.rsi > 70:
                parts.append("rsiOB")
            elif self.context.rsi < 30:
                parts.append("rsiOS")
            else:
                parts.append("rsiN")

        if self.context.macd_histogram is not None:
            parts.append("macd+" if self.context.macd_histogram > 0 else "macd-")

        if self.formations:
            parts.extend(sorted(self.formations)[:2])  # top 2 formations

        if self.context.funding_rate is not None:
            if self.context.funding_rate > 0.00005:
                parts.append("fHigh")
            elif self.context.funding_rate < -0.00005:
                parts.append("fNeg")
            else:
                parts.append("fNeut")

        return "|".join(parts)


# ---------------------------------------------------------------------------
# Extract all patterns from a candle series
# ---------------------------------------------------------------------------

def extract_patterns(
    candles: list[dict],
    indicators: dict,
    funding_rate: Optional[float] = None,
    funding_trend: Optional[str] = None,
) -> list[PatternSnapshot]:
    """Extract PatternSnapshots for every candle that has enough indicator data.

    Requires at least 50 candles of warm-up for SMA(50).
    """
    if len(candles) < 50:
        return []

    patterns = []
    atr_vals = indicators["atr_14"]
    vols = indicators["volumes"]

    for i in range(50, len(candles)):
        candle = candles[i]
        atr = atr_vals[i]

        # Volume SMA(20)
        vol_start = max(0, i - 19)
        vol_window = [v for v in vols[vol_start:i + 1] if v > 0]
        vol_sma = sum(vol_window) / len(vol_window) if vol_window else 1.0

        morph = classify_candle(candle, atr, vol_sma)
        formations = detect_formations(candles, i)
        ctx = compute_context(candle, indicators, i, funding_rate, funding_trend)

        patterns.append(PatternSnapshot(
            timestamp=candle["timestamp"],
            price=candle["close"],
            morphology=morph,
            formations=formations,
            context=ctx,
        ))

    return patterns
