"""
main.py — FastAPI backend for Oanda Trading Center.

Routes:
  GET  /api/health          Server status
  GET  /api/account         Account summary + daily P&L
  GET  /api/signals         Full H4 signal scan (all 16 instruments)
  GET  /api/prices          Live bid/ask for all 16 instruments
  GET  /api/trades          Open trades with live P&L
  GET  /api/positions       Open positions
  GET  /api/journal         Trade journal from Supabase
  GET  /api/news            Economic calendar / news check
  GET  /api/calculator      Position size calculation
  POST /api/place-order     Place a market order
  POST /api/close-trade     Close a specific trade
  POST /api/breakeven       Move SL to entry price
  POST /api/trailing-stop   Set trailing stop on a trade
  POST /api/modify-trade    Modify SL and/or TP on a trade
"""

import logging
import threading
import time as _time
import urllib.request as _req
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from oandapyV20.exceptions import V20Error
from pydantic import BaseModel

from api import oanda, signals
from api.pip_utils import atr_to_pips, get_pip, price_decimals, sl_tp_prices

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Oanda Trading Center API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Keep-alive: ping every 14 mins so Render free tier never sleeps
# ─────────────────────────────────────────────────────────────────────────────

def _keep_alive():
    _time.sleep(60)  # wait for server to fully start first
    while True:
        try:
            _req.urlopen(
                "https://oanda-trading-center.onrender.com/api/health",
                timeout=10,
            )
            logger.debug("Keep-alive ping sent")
        except Exception:
            pass
        _time.sleep(840)  # 14 minutes


threading.Thread(target=_keep_alive, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class PlaceOrderRequest(BaseModel):
    instrument: str
    direction: str       # "BUY" or "SELL"
    units: int
    sl_price: float
    tp_price: float


class CloseTradeRequest(BaseModel):
    trade_id: str


class BreakevenRequest(BaseModel):
    trade_id: str


class TrailingStopRequest(BaseModel):
    trade_id: str
    trail_pips: float | None = None
    atr: float | None = None
    instrument: str | None = None


class ModifyTradeRequest(BaseModel):
    trade_id: str
    instrument: str
    sl_price: float | None = None
    tp_price: float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status":     "ok",
        "time":       datetime.now(timezone.utc).isoformat(),
        "version":    "2.0.0",
        "session":    signals.get_current_session(),
        "in_session": signals.is_trading_session(),
    }


@app.get("/api/account")
def account():
    try:
        summary   = oanda.get_account_summary()
        daily_pnl = oanda.get_daily_pnl()
        return {
            "ok":        True,
            "account":   summary,
            "daily_pnl": daily_pnl,
        }
    except Exception as e:
        logger.error(f"/api/account error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals")
def get_signals():
    """Scan all 16 instruments. Returns signals with confluence scores 0-7."""
    try:
        results = signals.scan_all()
        fired   = [r for r in results if r.get("signal") in ("BUY", "SELL")]
        return {
            "scanned":    len(results),
            "fired":      len(fired),
            "session":    signals.get_current_session(),
            "in_session": signals.is_trading_session(),
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "signals":    results,
        }
    except Exception as e:
        logger.error(f"/api/signals error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prices")
def get_prices():
    """Live bid/ask for all 16 instruments in ONE Oanda API call."""
    try:
        return oanda.get_all_prices(signals.INSTRUMENTS)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
def get_trades():
    """Open trades with current P&L."""
    try:
        return {"trades": oanda.get_open_trades()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
def get_positions():
    try:
        return {"positions": oanda.get_open_positions()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/journal")
def get_journal():
    """
    Trade journal stats. Tries Supabase first, falls back to
    Oanda closed trade history if Supabase is unavailable.
    """
    try:
        # Try Supabase client if available
        try:
            from api.supabase_client import get_journal_stats
            data = get_journal_stats()
            if data:
                return data
        except Exception:
            pass

        # Fallback: build stats from Oanda closed trades
        history = oanda.get_trade_history(count=100)
        closed  = [t for t in history if t.get("state") == "CLOSED"]
        wins    = [t for t in closed if float(t.get("realizedPL", 0)) > 0]
        losses  = [t for t in closed if float(t.get("realizedPL", 0)) <= 0]
        total_pnl = sum(float(t.get("realizedPL", 0)) for t in closed)

        by_instrument: dict = {}
        for t in closed:
            inst = t.get("instrument", "UNKNOWN")
            if inst not in by_instrument:
                by_instrument[inst] = {"wins": 0, "losses": 0, "pnl": 0.0}
            pl = float(t.get("realizedPL", 0))
            if pl > 0:
                by_instrument[inst]["wins"] += 1
            else:
                by_instrument[inst]["losses"] += 1
            by_instrument[inst]["pnl"] = round(
                by_instrument[inst]["pnl"] + pl, 2
            )

        return {
            "total_trades":  len(history),
            "closed_trades": len(closed),
            "open_trades":   len(history) - len(closed),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl":     round(total_pnl, 2),
            "by_instrument": by_instrument,
            "recent":        list(reversed(closed[:20])),
            "trades":        closed,
        }
    except Exception as e:
        logger.error(f"/api/journal error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/news")
def get_news():
    """
    Economic calendar news check.
    Tries news_check module first, returns safe fallback if unavailable.
    """
    try:
        from api.news_check import get_upcoming_news
        return get_upcoming_news()
    except Exception:
        # Safe fallback — frontend can handle this gracefully
        return {
            "events":       [],
            "high_impact":  False,
            "next_event":   None,
            "warning":      "News feed unavailable — check FMP API key",
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }


@app.post("/api/place-order")
def place_order(req: PlaceOrderRequest):
    """
    Place a market order.
    sl_price and tp_price must be ABSOLUTE PRICE LEVELS, not pip distances.
    Use /api/calculator first to get the correct levels.
    """
    try:
        units  = req.units if req.direction == "BUY" else -req.units
        result = oanda.place_order(
            instrument=req.instrument,
            units=units,
            sl_price=req.sl_price,
            tp_price=req.tp_price,
        )
        return {"success": True, "order": result}
    except V20Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/close-trade")
def close_trade(req: CloseTradeRequest):
    try:
        result = oanda.close_trade(req.trade_id)
        return {"success": True, "result": result}
    except V20Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/breakeven")
def breakeven(req: BreakevenRequest):
    """
    Move stop loss to entry price (breakeven).
    Trigger when trade reaches +1R profit.
    """
    try:
        result = oanda.move_to_breakeven(req.trade_id)
        return {"success": True, **result}
    except V20Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trailing-stop")
def trailing_stop(req: TrailingStopRequest):
    """Convert a fixed SL to a trailing stop."""
    try:
        result = oanda.set_trailing_stop(
            trade_id=req.trade_id,
            trail_pips=req.trail_pips,
            atr=req.atr,
            instrument=req.instrument,
        )
        return {"success": True, **result}
    except V20Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/modify-trade")
def modify_trade(req: ModifyTradeRequest):
    try:
        result = oanda.modify_sl_tp(
            trade_id=req.trade_id,
            sl_price=req.sl_price,
            tp_price=req.tp_price,
            instrument=req.instrument,
        )
        return {"success": True, "result": result}
    except V20Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/calculator")
def calculator(
    instrument: str,
    direction: str,
    account_balance: float,
    risk_pct: float = 1.0,
):
    """
    Position sizing calculator.
    Returns: units, sl_price, tp_price, risk_amount, rr.
    """
    try:
        import pandas as pd

        price_data = oanda.get_live_price(instrument)
        if not price_data:
            raise HTTPException(
                status_code=400,
                detail=f"Could not get live price for {instrument}",
            )

        candles = oanda.get_candles(instrument, "H4", count=20)
        if len(candles) < 15:
            raise HTTPException(
                status_code=400,
                detail="Insufficient candle data for ATR calculation",
            )

        df  = pd.DataFrame(candles)
        hl  = df["high"] - df["low"]
        hc  = (df["high"] - df["close"].shift()).abs()
        lc  = (df["low"]  - df["close"].shift()).abs()
        atr = float(
            pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean().iloc[-1]
        )

        entry  = price_data["ask"] if direction == "BUY" else price_data["bid"]
        pip    = get_pip(instrument)
        dp     = price_decimals(instrument)
        levels = sl_tp_prices(entry, direction, atr, instrument)

        risk_amount = round(account_balance * risk_pct / 100, 2)
        sl_dist     = abs(entry - levels["sl"])
        units       = max(1, int(risk_amount / sl_dist)) if sl_dist > 0 else 1

        return {
            "instrument":      instrument,
            "direction":       direction,
            "entry":           round(entry, dp),
            "sl":              levels["sl"],
            "tp":              levels["tp"],
            "sl_pips":         levels["sl_pips"],
            "tp_pips":         levels["tp_pips"],
            "rr":              levels["rr"],
            "atr":             round(atr, dp),
            "pip":             pip,
            "units":           units,
            "risk_amount":     risk_amount,
            "risk_pct":        risk_pct,
            "account_balance": account_balance,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (local dev: uvicorn api.main:app --reload)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)