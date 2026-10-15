"""
Technical Analysis Module
Computes indicators: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, VWAP, correlations.
All computations use pure Python + standard lib (no pandas/numpy dependency required).
"""

from typing import Optional
import math


def sma(closes: list[float], period: int) -> list[Optional[float]]:
    """Simple Moving Average."""
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        result[i] = sum(window) / period
    return result


def ema(closes: list[float], period: int) -> list[Optional[float]]:
    """Exponential Moving Average."""
    result: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period:
        return result
    k = 2 / (period + 1)
    # seed with SMA
    result[period - 1] = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """Relative Strength Index (Wilder's smoothing)."""
    result: list[Optional[float]] = [None] * len(closes)
    if len(closes) < period + 1:
        return result

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100 - (100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100 - (100 / (1 + rs))

    return result


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, list[Optional[float]]]:
    """MACD: line, signal, histogram."""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    macd_line: list[Optional[float]] = [None] * len(closes)
    for i in range(len(closes)):
        if ema_fast[i] is not None and ema_slow[i] is not None:
            macd_line[i] = ema_fast[i] - ema_slow[i]

    # Signal line = EMA of MACD line
    macd_values = [v for v in macd_line if v is not None]
    signal_line = [None] * len(closes)
    histogram = [None] * len(closes)

    if len(macd_values) >= signal_period:
        first_valid = next(i for i, v in enumerate(macd_line) if v is not None)
        ema_signal = ema(macd_values, signal_period)

        j = 0
        for i in range(first_valid, len(closes)):
            if j < len(ema_signal) and ema_signal[j] is not None:
                signal_line[i] = ema_signal[j]
                if macd_line[i] is not None:
                    histogram[i] = macd_line[i] - ema_signal[j]
            j += 1

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def bollinger_bands(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> dict[str, list[Optional[float]]]:
    """Bollinger Bands: upper, middle (SMA), lower."""
    middle = sma(closes, period)
    upper: list[Optional[float]] = [None] * len(closes)
    lower: list[Optional[float]] = [None] * len(closes)

    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        mean = middle[i]
        variance = sum((x - mean) ** 2 for x in window) / period
        std = math.sqrt(variance)
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std

    return {"upper": upper, "middle": middle, "lower": lower}


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[Optional[float]]:
    """Average True Range."""
    result: list[Optional[float]] = [None] * len(closes)
    if len(closes) < 2:
        return result

    true_ranges = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return result

    result[period - 1] = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        result[i] = (result[i - 1] * (period - 1) + true_ranges[i]) / period

    return result


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> dict[str, list[Optional[float]]]:
    """Average Directional Index (ADX) with +DI and -DI.

    Returns dict with 'adx', 'plus_di', 'minus_di' arrays.
    ADX < 20 = no trend (sideways), ADX > 25 = trending, ADX > 40 = strong trend.
    """
    n = len(closes)
    adx_result: list[Optional[float]] = [None] * n
    plus_di: list[Optional[float]] = [None] * n
    minus_di: list[Optional[float]] = [None] * n

    if n < period + 1:
        return {"adx": adx_result, "plus_di": plus_di, "minus_di": minus_di}

    # True Range, +DM, -DM
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []

    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm_list.append(up_move)
        else:
            plus_dm_list.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm_list.append(down_move)
        else:
            minus_dm_list.append(0.0)

    # Smoothed TR, +DM, -DM (Wilder's smoothing)
    smooth_tr = sum(tr_list[:period])
    smooth_plus = sum(plus_dm_list[:period])
    smooth_minus = sum(minus_dm_list[:period])

    dx_list = []

    for i in range(period - 1, len(tr_list)):
        if i == period - 1:
            pass  # already initialized
        else:
            smooth_tr = smooth_tr - smooth_tr / period + tr_list[i]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm_list[i]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm_list[i]

        if smooth_tr == 0:
            pdi = 0.0
            mdi = 0.0
        else:
            pdi = (smooth_plus / smooth_tr) * 100
            mdi = (smooth_minus / smooth_tr) * 100

        # i in tr_list corresponds to candle i+1
        candle_idx = i + 1
        plus_di[candle_idx] = pdi
        minus_di[candle_idx] = mdi

        if pdi + mdi == 0:
            dx = 0.0
        else:
            dx = abs(pdi - mdi) / (pdi + mdi) * 100
        dx_list.append((candle_idx, dx))

    # ADX = smoothed DX over `period`
    if len(dx_list) >= period:
        adx_val = sum(d for _, d in dx_list[:period]) / period
        adx_result[dx_list[period - 1][0]] = adx_val

        for j in range(period, len(dx_list)):
            adx_val = (adx_val * (period - 1) + dx_list[j][1]) / period
            adx_result[dx_list[j][0]] = adx_val

    return {"adx": adx_result, "plus_di": plus_di, "minus_di": minus_di}


def parabolic_sar(
    highs: list[float],
    lows: list[float],
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.20,
) -> list[Optional[dict]]:
    """Parabolic SAR. Each element is {'value': sar, 'trend': 'bull'|'bear'} or None."""
    n = len(highs)
    result: list[Optional[dict]] = [None] * n
    if n < 2:
        return result
    trend, sar, ep, af = "bull", lows[0], highs[0], af_start
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if trend == "bull":
            sar = min(sar, lows[i - 1], lows[i - 2] if i >= 2 else lows[i - 1])
            if lows[i] < sar:
                trend, sar, ep, af = "bear", ep, lows[i], af_start
            elif highs[i] > ep:
                ep, af = highs[i], min(af + af_step, af_max)
        else:
            sar = max(sar, highs[i - 1], highs[i - 2] if i >= 2 else highs[i - 1])
            if highs[i] > sar:
                trend, sar, ep, af = "bull", ep, highs[i], af_start
            elif lows[i] < ep:
                ep, af = lows[i], min(af + af_step, af_max)
        result[i] = {"value": sar, "trend": trend}
    return result


def vwap(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> list[Optional[float]]:
    """Volume Weighted Average Price (cumulative)."""
    result: list[Optional[float]] = [None] * len(closes)
    cum_vol = 0.0
    cum_tp_vol = 0.0

    for i in range(len(closes)):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_vol += volumes[i]
        cum_tp_vol += tp * volumes[i]
        if cum_vol > 0:
            result[i] = cum_tp_vol / cum_vol

    return result


def correlation(series_a: list[float], series_b: list[float]) -> Optional[float]:
    """Pearson correlation between two price series (returns)."""
    n = min(len(series_a), len(series_b))
    if n < 3:
        return None

    returns_a = [(series_a[i] - series_a[i - 1]) / series_a[i - 1] for i in range(1, n) if series_a[i - 1] != 0]
    returns_b = [(series_b[i] - series_b[i - 1]) / series_b[i - 1] for i in range(1, n) if series_b[i - 1] != 0]

    n_r = min(len(returns_a), len(returns_b))
    if n_r < 3:
        return None

    mean_a = sum(returns_a[:n_r]) / n_r
    mean_b = sum(returns_b[:n_r]) / n_r

    cov = sum((returns_a[i] - mean_a) * (returns_b[i] - mean_b) for i in range(n_r)) / n_r
    std_a = math.sqrt(sum((returns_a[i] - mean_a) ** 2 for i in range(n_r)) / n_r)
    std_b = math.sqrt(sum((returns_b[i] - mean_b) ** 2 for i in range(n_r)) / n_r)

    if std_a == 0 or std_b == 0:
        return None
    return cov / (std_a * std_b)


def compute_all_indicators(candles: list[dict]) -> dict:
    """Compute all indicators for a candle dataset. Returns dict of indicator arrays."""
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]
    timestamps = [c["timestamp"] for c in candles]

    return {
        "timestamps": timestamps,
        "closes": closes,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "sma_20": sma(closes, 20),
        "sma_50": sma(closes, 50),
        "sma_200": sma(closes, 200),
        "ema_9": ema(closes, 9),
        "ema_12": ema(closes, 12),
        "ema_26": ema(closes, 26),
        "rsi_14": rsi(closes, 14),
        "macd": macd(closes),
        "bollinger": bollinger_bands(closes, 20),
        "atr_14": atr(highs, lows, closes, 14),
        "adx_14": adx(highs, lows, closes, 14),
        "vwap": vwap(highs, lows, closes, volumes),
    }


def generate_summary(pair: str, candles: list[dict], indicators: dict) -> str:
    """Generate a text summary of the current technical state."""
    if not candles:
        return f"{pair}: No data available"

    current_price = candles[-1]["close"]
    lines = [f"=== {pair} Technical Summary ==="]
    lines.append(f"Price: ${current_price:,.2f}")
    lines.append(f"Period: {candles[0]['timestamp'].strftime('%Y-%m-%d')} to {candles[-1]['timestamp'].strftime('%Y-%m-%d')}")
    lines.append(f"Candles: {len(candles)}")

    # Price change
    first_close = candles[0]["close"]
    pct_change = ((current_price - first_close) / first_close) * 100
    lines.append(f"Period change: {pct_change:+.2f}%")

    # High/Low
    period_high = max(c["high"] for c in candles)
    period_low = min(c["low"] for c in candles)
    lines.append(f"Period high: ${period_high:,.2f}  |  Period low: ${period_low:,.2f}")

    # EMA / SMA signals
    ema9 = indicators["ema_9"][-1]
    sma20 = indicators["sma_20"][-1]
    sma200 = indicators["sma_200"][-1]

    if ema9 is not None:
        pos = "above" if current_price > ema9 else "below"
        lines.append(f"EMA(9):   ${ema9:,.2f} — price is {pos}")
    if sma20 is not None:
        pos = "above" if current_price > sma20 else "below"
        lines.append(f"SMA(20):  ${sma20:,.2f} — price is {pos}")
    if sma200 is not None:
        pos = "above" if current_price > sma200 else "below"
        lines.append(f"SMA(200): ${sma200:,.2f} — price is {pos}")

    # EMA9 vs SMA20 momentum
    if ema9 is not None and sma20 is not None:
        if ema9 > sma20:
            lines.append("EMA9/SMA20: BULLISH momentum (EMA9 > SMA20)")
        else:
            lines.append("EMA9/SMA20: BEARISH momentum (EMA9 < SMA20)")

    # Global trend filter (SMA 200)
    if sma200 is not None:
        if current_price > sma200:
            lines.append("SMA(200) trend: BULL MARKET (price above)")
        else:
            lines.append("SMA(200) trend: BEAR MARKET (price below)")

    # Pullback detection
    if ema9 is not None and sma20 is not None and sma200 is not None:
        if current_price > sma200 and current_price <= ema9 * 1.005 and current_price >= ema9 * 0.995:
            lines.append("SETUP: Pullback to EMA9 in uptrend — watch for bounce")
        elif current_price < sma200 and current_price <= ema9 * 1.005 and current_price >= ema9 * 0.995:
            lines.append("SETUP: Pullback to EMA9 in downtrend — watch for rejection")

    # ADX (trend strength)
    adx_data = indicators["adx_14"]
    adx_val = adx_data["adx"][-1]
    plus_di = adx_data["plus_di"][-1]
    minus_di = adx_data["minus_di"][-1]
    if adx_val is not None:
        if adx_val < 20:
            regime = "SIDEWAYS — no trend, avoid MA/SAR strategies"
        elif adx_val < 25:
            regime = "WEAK TREND — use caution"
        elif adx_val < 40:
            regime = "TRENDING — MA/SAR strategies work"
        else:
            regime = "STRONG TREND — aggressive positioning OK"
        lines.append(f"ADX(14):  {adx_val:.1f} — {regime}")
        if plus_di is not None and minus_di is not None:
            di_signal = "+DI dominant (bullish)" if plus_di > minus_di else "-DI dominant (bearish)"
            lines.append(f"+DI: {plus_di:.1f}  -DI: {minus_di:.1f} — {di_signal}")

    # RSI
    rsi_val = indicators["rsi_14"][-1]
    if rsi_val is not None:
        zone = "OVERBOUGHT" if rsi_val > 70 else "OVERSOLD" if rsi_val < 30 else "NEUTRAL"
        lines.append(f"RSI(14): {rsi_val:.1f} — {zone}")

    # MACD
    macd_data = indicators["macd"]
    macd_val = macd_data["macd"][-1]
    signal_val = macd_data["signal"][-1]
    hist_val = macd_data["histogram"][-1]
    if macd_val is not None and signal_val is not None:
        signal = "BULLISH" if macd_val > signal_val else "BEARISH"
        lines.append(f"MACD: {macd_val:.2f}  Signal: {signal_val:.2f}  Hist: {hist_val:+.2f} — {signal}")

    # Bollinger
    bb = indicators["bollinger"]
    bb_upper = bb["upper"][-1]
    bb_lower = bb["lower"][-1]
    if bb_upper is not None:
        bb_width = ((bb_upper - bb_lower) / bb["middle"][-1]) * 100
        if current_price > bb_upper:
            bb_pos = "ABOVE upper band (overbought)"
        elif current_price < bb_lower:
            bb_pos = "BELOW lower band (oversold)"
        else:
            bb_pos = "within bands"
        lines.append(f"Bollinger: [{bb_lower:,.2f} — {bb_upper:,.2f}] width={bb_width:.1f}% — {bb_pos}")

    # ATR (volatility)
    atr_val = indicators["atr_14"][-1]
    if atr_val is not None:
        atr_pct = (atr_val / current_price) * 100
        lines.append(f"ATR(14): ${atr_val:,.2f} ({atr_pct:.2f}% of price)")

    # Volume
    avg_vol = sum(indicators["volumes"][-20:]) / min(20, len(indicators["volumes"]))
    latest_vol = indicators["volumes"][-1]
    vol_ratio = latest_vol / avg_vol if avg_vol > 0 else 0
    lines.append(f"Volume: {latest_vol:,.0f} (vs 20-period avg: {avg_vol:,.0f}, ratio: {vol_ratio:.2f}x)")

    return "\n".join(lines)
