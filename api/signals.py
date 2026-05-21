"""
signals.py — H4 confluence signal scanner for all 16 instruments.

FIXES vs previous version:
  1. Uses pip_utils.get_pip() — no more wrong pip values
  2. Adds H1 RSI confirmation (your rule: H1 RSI < 50 for BUY, > 50 for SELL)
  3. Returns confluence_score (how many of 7 conditions were met)
  4. Adds session filter (London 07-16 UTC, New York 13-21 UTC)
  5. ATR multipliers now match your rules: SL=1.5x, TP=2.5x
"""

import pandas as pd
import logging
from datetime import datetime, timezone
from api.oanda import get_candles
from api.pip_utils import get_pip, price_decimals, sl_tp_prices

logger = logging.getLogger(__name__)

INSTRUMENTS = [
    "EUR_USD", "GBP_JPY",
    "XAU_USD", "XAG_USD", "XPD_USD",
    "NATGAS_USD", "WTICO_USD",
    "CORN_USD", "SUGAR_USD", "WHEAT_USD", "SOYBN_USD",
    "SPX500_USD", "NAS100_USD", "UK100_GBP", "DE30_EUR",
]


def _calc_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _calc_ema(prices: pd.Series, period: int) -> pd.Series:
    return prices.ewm(span=period, adjust=False).mean()


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(period).mean()


def _calc_macd(prices: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def is_trading_session() -> bool:
    """Returns True if current UTC time is within London or NY session."""
    now_h = datetime.now(timezone.utc).hour
    london = 7 <= now_h < 16
    new_york = 13 <= now_h < 21
    return london or new_york


def get_current_session() -> str:
    now_h = datetime.now(timezone.utc).hour
    if 13 <= now_h < 16:
        return "London/NY Overlap"
    if 7 <= now_h < 16:
        return "London"
    if 16 <= now_h < 21:
        return "New York"
    return "Off-session"


def analyse_instrument(instrument: str) -> dict:
    """
    Run the full H4 confluence check + H1 confirmation for one instrument.
    Returns a signal dict with score, reasons, and SL/TP prices.
    """
    base = {
        "instrument": instrument,
        "signal": "HOLD",
        "confluence_score": 0,
        "max_score": 7,
        "reasons": [],
        "h1_confirmed": False,
        "session": get_current_session(),
        "in_session": is_trading_session(),
    }

    # ── H4 candles ──────────────────────────────────────────────────────────
    try:
        h4_candles = get_candles(instrument, "H4", count=250)
    except Exception as e:
        logger.error(f"{instrument} H4 fetch failed: {e}")
        return {**base, "signal": "ERROR", "error": str(e)}

    if len(h4_candles) < 210:
        return {**base, "signal": "INSUFFICIENT_DATA"}

    df = pd.DataFrame(h4_candles)
    df["rsi"]      = _calc_rsi(df["close"], 14)
    df["ema50"]    = _calc_ema(df["close"], 50)
    df["ema200"]   = _calc_ema(df["close"], 200)
    df["atr"]      = _calc_atr(df, 14)
    df["vol_ma"]   = df["volume"].rolling(20).mean()
    df["don_high"] = df["high"].rolling(20).max()
    df["don_low"]  = df["low"].rolling(20).min()
    df["macd"], df["macd_signal"] = _calc_macd(df["close"])

    l  = df.iloc[-1]   # latest closed H4 candle
    p  = df.iloc[-2]   # previous candle (for crossover checks)

    # ── H1 candles (confirmation timeframe) ─────────────────────────────────
    h1_rsi = None
    try:
        h1_candles = get_candles(instrument, "H1", count=20)
        if len(h1_candles) >= 15:
            h1_df = pd.DataFrame(h1_candles)
            h1_df["rsi"] = _calc_rsi(h1_df["close"], 14)
            h1_rsi = h1_df["rsi"].iloc[-1]
    except Exception as e:
        logger.warning(f"{instrument} H1 fetch failed: {e}")

    # ── Evaluate 7 confluence conditions ────────────────────────────────────
    score = 0
    buy_reasons  = []
    sell_reasons = []

    # 1. RSI oversold/overbought
    c1_buy  = l["rsi"] < 38
    c1_sell = l["rsi"] > 62
    if c1_buy:  buy_reasons.append(f"RSI {l['rsi']:.1f} — oversold (<38)")
    if c1_sell: sell_reasons.append(f"RSI {l['rsi']:.1f} — overbought (>62)")

    # 2. Price vs 200 EMA (trend filter)
    c2_buy  = l["close"] > l["ema200"]
    c2_sell = l["close"] < l["ema200"]
    if c2_buy:  buy_reasons.append(f"Price above 200 EMA ({l['ema200']:.{price_decimals(instrument)}f})")
    if c2_sell: sell_reasons.append(f"Price below 200 EMA ({l['ema200']:.{price_decimals(instrument)}f})")

    # 3. 50 EMA vs 200 EMA (golden/death cross)
    c3_buy  = l["ema50"] > l["ema200"]
    c3_sell = l["ema50"] < l["ema200"]
    if c3_buy:  buy_reasons.append("50 EMA above 200 EMA — golden cross")
    if c3_sell: sell_reasons.append("50 EMA below 200 EMA — death cross")

    # 4. MACD crossed above/below zero
    c4_buy  = p["macd"] <= 0 and l["macd"] > 0
    c4_sell = p["macd"] >= 0 and l["macd"] < 0
    if c4_buy:  buy_reasons.append("MACD crossed above zero — bullish momentum")
    if c4_sell: sell_reasons.append("MACD crossed below zero — bearish momentum")

    # 5. Donchian breakout
    c5_buy  = l["close"] > p["don_high"]
    c5_sell = l["close"] < p["don_low"]
    if c5_buy:  buy_reasons.append(f"Donchian breakout above {p['don_high']:.{price_decimals(instrument)}f}")
    if c5_sell: sell_reasons.append(f"Donchian breakdown below {p['don_low']:.{price_decimals(instrument)}f}")

    # 6. Volume surge (above 20-period MA)
    c6 = l["volume"] > l["vol_ma"] * 1.1 if l["vol_ma"] > 0 else False
    if c6:
        buy_reasons.append("Volume surge — confirms move")
        sell_reasons.append("Volume surge — confirms move")

    # 7. H1 RSI confirmation
    c7_buy  = h1_rsi is not None and h1_rsi < 50
    c7_sell = h1_rsi is not None and h1_rsi > 50

    # ── Determine signal ─────────────────────────────────────────────────────
    buy_conditions  = [c1_buy,  c2_buy,  c3_buy,  c4_buy,  c5_buy,  c6, c7_buy]
    sell_conditions = [c1_sell, c2_sell, c3_sell, c4_sell, c5_sell, c6, c7_sell]

    buy_score  = sum(buy_conditions)
    sell_score = sum(sell_conditions)

    signal    = "HOLD"
    reasons   = []
    score     = 0
    h1_ok     = False

    if buy_score >= 3 and buy_score >= sell_score:
        signal  = "BUY"
        reasons = buy_reasons
        score   = buy_score
        h1_ok   = c7_buy
        if h1_rsi is not None:
            reasons.append(f"H1 RSI {h1_rsi:.1f} {'✓ confirms' if c7_buy else '✗ not confirmed'}")
    elif sell_score >= 3:
        signal  = "SELL"
        reasons = sell_reasons
        score   = sell_score
        h1_ok   = c7_sell
        if h1_rsi is not None:
            reasons.append(f"H1 RSI {h1_rsi:.1f} {'✓ confirms' if c7_sell else '✗ not confirmed'}")

    # ── SL / TP ──────────────────────────────────────────────────────────────
    atr   = float(l["atr"])
    entry = float(l["close"])
    levels = sl_tp_prices(entry, signal if signal != "HOLD" else "BUY",
                          atr, instrument, sl_multiplier=1.5, tp_multiplier=2.5)

    return {
        "instrument":      instrument,
        "signal":          signal,
        "confluence_score": score,
        "max_score":       7,
        "h1_confirmed":    h1_ok,
        "h1_rsi":          round(h1_rsi, 1) if h1_rsi is not None else None,
        "session":         get_current_session(),
        "in_session":      is_trading_session(),
        "price":           round(entry, price_decimals(instrument)),
        "rsi":             round(float(l["rsi"]), 1),
        "atr":             round(atr, price_decimals(instrument)),
        "ema50":           round(float(l["ema50"]), price_decimals(instrument)),
        "ema200":          round(float(l["ema200"]), price_decimals(instrument)),
        "sl":              levels["sl"],
        "tp":              levels["tp"],
        "sl_pips":         levels["sl_pips"],
        "tp_pips":         levels["tp_pips"],
        "rr":              levels["rr"],
        "reasons":         reasons,
        "pip":             get_pip(instrument),
    }


def scan_all() -> list[dict]:
    """Scan all 16 instruments and return list of signal dicts."""
    results = []
    for inst in INSTRUMENTS:
        try:
            result = analyse_instrument(inst)
            results.append(result)
            logger.info(f"Scanned {inst}: {result['signal']} score={result['confluence_score']}/7")
        except Exception as e:
            logger.error(f"Failed to scan {inst}: {e}")
            results.append({
                "instrument": inst,
                "signal": "ERROR",
                "error": str(e),
                "confluence_score": 0,
            })
    return results
