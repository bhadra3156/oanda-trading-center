"""
oanda.py — Oanda fxTrade v20 client.

FIXES vs previous version:
  1. Uses pip_utils.get_pip() everywhere — no more hardcoded 0.01 / 0.0001
  2. Adds /breakeven endpoint: moves SL to entry price
  3. Adds /trailing-stop endpoint: converts fixed SL to ATR trailing stop
  4. place_order now rounds SL/TP to correct decimal places per instrument
  5. Daily P&L now uses correct 3% daily / 5% weekly limits
"""

import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import oandapyV20
from oandapyV20 import API
from oandapyV20.exceptions import V20Error
import oandapyV20.endpoints.accounts   as accounts
import oandapyV20.endpoints.orders     as orders
import oandapyV20.endpoints.trades     as trades
import oandapyV20.endpoints.positions  as positions
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing    as pricing
from oandapyV20.contrib.requests import (
    MarketOrderRequest,
    TakeProfitDetails,
    StopLossDetails,
    TrailingStopLossDetails,
)
from api.pip_utils import get_pip, price_decimals

load_dotenv()
logger = logging.getLogger(__name__)

_api_key     = os.getenv("OANDA_API_KEY")
_account_id  = os.getenv("OANDA_ACCOUNT_ID")
_environment = os.getenv("OANDA_ENVIRONMENT", "practice")

if not _api_key or not _account_id:
    raise ValueError("Set OANDA_API_KEY and OANDA_ACCOUNT_ID in your .env file")

_client = API(access_token=_api_key, environment=_environment)
logger.info(f"Oanda client ready [{_environment.upper()}]")


# ─────────────────────────────────────────────────────────────────────────────
# READ OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_account_summary() -> dict:
    r = accounts.AccountSummary(_account_id)
    _client.request(r)
    return r.response.get("account", {})


def get_live_price(instrument: str) -> dict:
    r = pricing.PricingInfo(accountID=_account_id, params={"instruments": instrument})
    _client.request(r)
    prices = r.response.get("prices", [])
    if not prices:
        return {}
    p = prices[0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return {
        "instrument": instrument,
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2, price_decimals(instrument)),
        "spread": round(ask - bid, price_decimals(instrument)),
        "spread_pips": round((ask - bid) / get_pip(instrument), 1),
        "time": p.get("time"),
    }


def get_candles(instrument: str, granularity: str = "H4", count: int = 250) -> list[dict]:
    r = instruments.InstrumentsCandles(
        instrument=instrument,
        params={"count": count, "granularity": granularity},
    )
    _client.request(r)
    result = []
    for c in r.response.get("candles", []):
        if c.get("complete", True):
            mid = c.get("mid", {})
            result.append({
                "time":   c["time"],
                "open":   float(mid.get("o", 0)),
                "high":   float(mid.get("h", 0)),
                "low":    float(mid.get("l", 0)),
                "close":  float(mid.get("c", 0)),
                "volume": int(c.get("volume", 0)),
            })
    return result


def get_open_trades() -> list[dict]:
    r = trades.OpenTrades(_account_id)
    _client.request(r)
    return r.response.get("trades", [])


def get_trade_history(count: int = 50) -> list[dict]:
    r = trades.TradesList(_account_id, params={"state": "CLOSED", "count": count})
    _client.request(r)
    return r.response.get("trades", [])


def get_open_positions() -> list[dict]:
    r = positions.OpenPositions(_account_id)
    _client.request(r)
    return r.response.get("positions", [])


def get_daily_pnl() -> dict:
    """
    Returns today's P&L with circuit breaker status.
    Uses YOUR rules: 3% daily limit, 5% weekly limit.
    """
    history  = get_trade_history(count=100)
    today    = str(datetime.now(timezone.utc).date())
    pnl      = 0.0
    wins = losses = 0

    for t in history:
        if t.get("closeTime", "")[:10] == today:
            pl = float(t.get("realizedPL", 0))
            pnl += pl
            if pl > 0:
                wins += 1
            else:
                losses += 1

    summary = get_account_summary()
    balance = float(summary.get("balance", 0))

    daily_limit  = balance * 0.03   # YOUR rule: 3%
    weekly_limit = balance * 0.05   # YOUR rule: 5%

    count = wins + losses
    return {
        "date":           today,
        "total_pnl":      round(pnl, 2),
        "trade_count":    count,
        "wins":           wins,
        "losses":         losses,
        "win_rate":       round(wins / count * 100, 1) if count else 0,
        "daily_limit":    round(daily_limit, 2),
        "weekly_limit":   round(weekly_limit, 2),
        "daily_used_pct": round(abs(pnl) / daily_limit * 100, 1) if daily_limit > 0 else 0,
        "daily_breached": pnl <= -daily_limit,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WRITE OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def place_order(
    instrument: str,
    units: int,
    sl_price: float | None = None,
    tp_price: float | None = None,
) -> dict:
    """
    Place a market order with optional SL/TP as absolute PRICES.

    Use pip_utils.sl_tp_prices() to calculate sl_price and tp_price
    before calling this function. Do NOT pass pip-based distances.
    """
    dp   = price_decimals(instrument)
    data = MarketOrderRequest(instrument=instrument, units=units).data

    if sl_price is not None:
        sl_rounded = round(sl_price, dp)
        data["order"]["stopLossOnFill"] = StopLossDetails(price=sl_rounded).data

    if tp_price is not None:
        tp_rounded = round(tp_price, dp)
        data["order"]["takeProfitOnFill"] = TakeProfitDetails(price=tp_rounded).data

    r = orders.OrderCreate(_account_id, data=data)
    _client.request(r)
    logger.info(f"Order placed: {instrument} {units} units | SL={sl_price} TP={tp_price}")
    return r.response


def close_trade(trade_id: str) -> dict:
    r = trades.TradeClose(_account_id, tradeID=trade_id)
    _client.request(r)
    return r.response


def close_all_trades() -> list[dict]:
    return [close_trade(t["id"]) for t in get_open_trades()]


def move_to_breakeven(trade_id: str) -> dict:
    """
    Move the stop loss of an open trade to its exact entry price.
    This implements your rule: 'Move SL to breakeven at 1:1 profit'.
    """
    open_trades = get_open_trades()
    trade = next((t for t in open_trades if t["id"] == trade_id), None)
    if not trade:
        raise ValueError(f"Trade {trade_id} not found in open trades")

    entry_price = float(trade["price"])
    instrument  = trade["instrument"]
    dp          = price_decimals(instrument)
    be_price    = round(entry_price, dp)

    body = {"stopLoss": {"price": str(be_price), "timeInForce": "GTC"}}
    r = trades.TradeCRCDO(_account_id, tradeID=trade_id, data=body)
    _client.request(r)
    logger.info(f"Breakeven set for trade {trade_id}: SL moved to {be_price}")
    return {
        "trade_id":       trade_id,
        "instrument":     instrument,
        "entry":          entry_price,
        "breakeven_price": be_price,
        "response":       r.response,
    }


def set_trailing_stop(trade_id: str, trail_pips: float | None = None, atr: float | None = None, instrument: str | None = None) -> dict:
    """
    Convert a trade's fixed SL to a trailing stop.

    Pass either:
      - trail_pips: explicit pip distance for the trail
      - atr + instrument: will use 1.0 × ATR as trail distance (converted to pips)
    """
    if trail_pips is None:
        if atr is None or instrument is None:
            raise ValueError("Provide trail_pips OR (atr + instrument)")
        trail_pips = atr / get_pip(instrument)

    distance = str(round(trail_pips, 1))
    body = {"trailingStopLoss": {"distance": distance, "timeInForce": "GTC"}}
    r = trades.TradeCRCDO(_account_id, tradeID=trade_id, data=body)
    _client.request(r)
    logger.info(f"Trailing stop set for trade {trade_id}: {trail_pips} pips")
    return {"trade_id": trade_id, "trail_pips": trail_pips, "response": r.response}


def modify_sl_tp(trade_id: str, sl_price: float | None = None, tp_price: float | None = None, instrument: str = "") -> dict:
    """Modify SL and/or TP on an open trade."""
    dp   = price_decimals(instrument) if instrument else 5
    body = {}
    if sl_price is not None:
        body["stopLoss"] = {"price": str(round(sl_price, dp)), "timeInForce": "GTC"}
    if tp_price is not None:
        body["takeProfit"] = {"price": str(round(tp_price, dp)), "timeInForce": "GTC"}
    r = trades.TradeCRCDO(_account_id, tradeID=trade_id, data=body)
    _client.request(r)
    return r.response

def get_all_prices(instruments: list[str]) -> dict:
    """Get live prices for ALL instruments in a single Oanda API call."""
    joined = ",".join(instruments)
    r = pricing.PricingInfo(accountID=_account_id, params={"instruments": joined})
    _client.request(r)
    result = {}
    for p in r.response.get("prices", []):
        inst = p["instrument"]
        bid  = float(p["bids"][0]["price"])
        ask  = float(p["asks"][0]["price"])
        result[inst] = {
            "instrument":  inst,
            "bid":         bid,
            "ask":         ask,
            "mid":         round((bid + ask) / 2, price_decimals(inst)),
            "spread":      round(ask - bid, price_decimals(inst)),
            "spread_pips": round((ask - bid) / get_pip(inst), 1),
            "time":        p.get("time"),
        }
    return result
