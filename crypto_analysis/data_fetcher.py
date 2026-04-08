"""
Coinbase Public API Data Fetcher
Fetches OHLCV candles, spot prices, and exchange rates without authentication.
"""

import requests
import time
from datetime import datetime, timedelta
from typing import Optional


COINBASE_EXCHANGE_BASE = "https://api.exchange.coinbase.com"
COINBASE_V2_BASE = "https://api.coinbase.com/v2"

# Coinbase granularity options (seconds)
GRANULARITY = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600,
    "1d": 86400,
}

DEFAULT_PAIRS = ["BTC-USD", "ETH-USD", "SOL-USD"]


def get_spot_price(pair: str = "BTC-USD") -> dict:
    """Get current spot price for a trading pair."""
    url = f"{COINBASE_V2_BASE}/prices/{pair}/spot"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()["data"]
    return {"pair": pair, "price": float(data["amount"]), "currency": data["currency"]}


def get_exchange_rates(base: str = "USD") -> dict:
    """Get exchange rates for a base currency."""
    url = f"{COINBASE_V2_BASE}/exchange-rates"
    resp = requests.get(url, params={"currency": base}, timeout=10)
    resp.raise_for_status()
    return resp.json()["data"]["rates"]


def get_candles(
    pair: str = "BTC-USD",
    granularity: str = "1h",
    days_back: int = 30,
) -> list[dict]:
    """
    Fetch OHLCV candles from Coinbase Exchange API.

    Coinbase returns max 300 candles per request, so we paginate if needed.
    Each candle: [timestamp, low, high, open, close, volume]
    """
    gran_seconds = GRANULARITY.get(granularity, 3600)
    max_candles_per_request = 300
    seconds_per_request = max_candles_per_request * gran_seconds

    end = datetime.utcnow()
    start = end - timedelta(days=days_back)

    all_candles = []
    current_start = start

    while current_start < end:
        current_end = min(current_start + timedelta(seconds=seconds_per_request), end)
        url = f"{COINBASE_EXCHANGE_BASE}/products/{pair}/candles"
        params = {
            "start": current_start.isoformat() + "Z",
            "end": current_end.isoformat() + "Z",
            "granularity": gran_seconds,
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()

        for candle in raw:
            ts, low, high, open_, close, volume = candle
            all_candles.append({
                "timestamp": datetime.utcfromtimestamp(ts),
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            })

        current_start = current_end
        time.sleep(0.2)  # respect rate limits

    all_candles.sort(key=lambda c: c["timestamp"])
    return all_candles


def get_available_pairs(quote: str = "USD") -> list[str]:
    """List available trading pairs for a quote currency."""
    url = f"{COINBASE_EXCHANGE_BASE}/products"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    products = resp.json()
    return sorted(
        p["id"] for p in products
        if p.get("quote_currency") == quote and p.get("status") == "online"
    )


def get_multi_pair_candles(
    pairs: Optional[list[str]] = None,
    granularity: str = "1h",
    days_back: int = 30,
) -> dict[str, list[dict]]:
    """Fetch candles for multiple pairs."""
    if pairs is None:
        pairs = DEFAULT_PAIRS
    result = {}
    for pair in pairs:
        print(f"  Fetching {pair} ({granularity}, {days_back}d)...")
        try:
            result[pair] = get_candles(pair, granularity, days_back)
            print(f"    -> {len(result[pair])} candles")
        except Exception as e:
            print(f"    -> Error: {e}")
            result[pair] = []
    return result


if __name__ == "__main__":
    # Quick test
    print("=== Spot Prices ===")
    for p in DEFAULT_PAIRS:
        info = get_spot_price(p)
        print(f"  {info['pair']}: ${info['price']:,.2f}")

    print("\n=== Candle Fetch Test (BTC-USD, 1h, 7 days) ===")
    candles = get_candles("BTC-USD", "1h", 7)
    print(f"  Got {len(candles)} candles")
    if candles:
        print(f"  First: {candles[0]['timestamp']} O={candles[0]['open']:.2f}")
        print(f"  Last:  {candles[-1]['timestamp']} C={candles[-1]['close']:.2f}")
