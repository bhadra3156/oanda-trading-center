"""
pip_utils.py — Correct pip/point values for all 16 Oanda instruments.

REPLACES the broken inline pip logic scattered across signals.py, oanda.py etc.
Import get_pip() everywhere you previously wrote:
    pip = 0.01 if "XAU" in instrument else 0.0001

Usage:
    from api.pip_utils import get_pip, price_decimals, sl_tp_prices
"""

import logging
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Ground truth: verified against Oanda v20 API
# pipLocation field = exponent → pip = 10^pipLocation
# ─────────────────────────────────────────────
_PIP_MAP: dict[str, float] = {
    # Forex
    "EUR_USD":    0.0001,   # pipLocation: -4
    "GBP_JPY":    0.01,     # pipLocation: -2  (JPY pair)

    # Metals
    "XAU_USD":    0.01,     # Gold       ~3200 | pipLocation: -2
    "XAG_USD":    0.001,    # Silver     ~32   | pipLocation: -3  ← WAS 0.0001 (10x error)
    "XPD_USD":    0.01,     # Palladium  ~1000 | pipLocation: -2  ← WAS 0.0001 (100x error)

    # Energy
    "NATGAS_USD": 0.0001,   # Nat Gas    ~3.50 | pipLocation: -4  ← WAS 0.01 (100x error)
    "WTICO_USD":  0.001,    # WTI Oil    ~70   | pipLocation: -3  ← WAS 0.01 (10x error)

    # Agriculture
    "CORN_USD":   0.0001,   # Corn       ~4.50 | pipLocation: -4
    "SUGAR_USD":  0.00001,  # Sugar      ~0.17 | pipLocation: -5  ← WAS 0.0001 (10x error)
    "WHEAT_USD":  0.0001,   # Wheat      ~5.50 | pipLocation: -4
    "SOYBN_USD":  0.0001,   # Soybeans   ~10   | pipLocation: -4

    # Indices — all quoted to 1 decimal place, point = 0.1
    "SPX500_USD": 0.1,      # S&P 500    ~5500 | pipLocation: -1  ← WAS 0.0001 (1000x error)
    "NAS100_USD": 0.1,      # Nasdaq     ~19000| pipLocation: -1  ← WAS 0.0001 (1000x error)
    "UK100_GBP":  0.1,      # FTSE 100   ~8500 | pipLocation: -1  ← WAS 0.0001 (1000x error)
    "DE30_EUR":   0.1,      # DAX        ~18000| pipLocation: -1  ← WAS 0.0001 (1000x error)
}

# Decimal places for rounding order prices
_DECIMAL_MAP: dict[str, int] = {
    "EUR_USD":    5,
    "GBP_JPY":    3,
    "XAU_USD":    3,
    "XAG_USD":    4,
    "XPD_USD":    3,
    "NATGAS_USD": 4,
    "WTICO_USD":  3,
    "CORN_USD":   4,
    "SUGAR_USD":  5,
    "WHEAT_USD":  4,
    "SOYBN_USD":  4,
    "SPX500_USD": 1,
    "NAS100_USD": 1,
    "UK100_GBP":  1,
    "DE30_EUR":   1,
}


def get_pip(instrument: str) -> float:
    """Return the pip/point size for an Oanda instrument string."""
    pip = _PIP_MAP.get(instrument)
    if pip is None:
        logger.warning(f"get_pip: unknown instrument '{instrument}', defaulting to 0.0001")
        return 0.0001
    return pip


def price_decimals(instrument: str) -> int:
    """Return the number of decimal places to round order prices to."""
    return _DECIMAL_MAP.get(instrument, 5)


def atr_to_pips(atr: float, instrument: str) -> float:
    """Convert ATR (in price units) to pips for the given instrument."""
    return round(atr / get_pip(instrument), 1)


def pips_to_price(pips: float, instrument: str) -> float:
    """Convert a pip count back to a price distance."""
    return pips * get_pip(instrument)


def sl_tp_prices(
    entry: float,
    direction: str,          # "BUY" or "SELL"
    atr: float,
    instrument: str,
    sl_multiplier: float = 1.5,
    tp_multiplier: float = 2.5,
) -> dict:
    """
    Calculate stop-loss and take-profit PRICES from ATR.

    Returns:
        {
            "sl":       float,   # absolute price level
            "tp":       float,   # absolute price level
            "sl_pips":  float,   # distance in pips
            "tp_pips":  float,   # distance in pips
            "rr":       float,   # reward:risk ratio
        }
    """
    dp       = price_decimals(instrument)
    pip      = get_pip(instrument)
    sl_dist  = atr * sl_multiplier
    tp_dist  = atr * tp_multiplier

    sign = 1 if direction == "BUY" else -1
    sl   = round(entry - sign * sl_dist, dp)
    tp   = round(entry + sign * tp_dist, dp)

    return {
        "sl":      sl,
        "tp":      tp,
        "sl_pips": round(sl_dist / pip, 1),
        "tp_pips": round(tp_dist / pip, 1),
        "rr":      round(tp_multiplier / sl_multiplier, 2),
    }
