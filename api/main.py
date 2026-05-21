"""
main.py — FastAPI backend for Oanda Trading Center.

Routes:
  GET  /api/health          Server status
  GET  /api/account         Account summary + daily P&L
  GET  /api/signals         Full H4 signal scan (all 16 instruments)
  GET  /api/prices          Live bid/ask for all 16 instruments
  GET  /api/trades          Open trades with live P&L
  GET  /api/positions       Open positions
  POST /api/place-order     Place a market order
  POST /api/close-trade     Close a specific trade
  POST /api/breakeven       Move SL to entry price
  POST /api/trailing-stop   Set trailing stop on a trade
  POST /api/modify-trade    Modify SL and/or TP on a trade
  GET  /api/journal         Trade journal stats from Supabase
  GET  /api/calculator      Position size calculation
"""

import logging
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from api import oanda, signals
from api.pip_utils import get_pip, price_decimals, sl_tp_prices, atr_to_pips

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
# Request / Response models
# ─────────────────────────────────────────────────────────────────────────────

class PlaceOrderRequest(BaseModel):
    instrument: str
    direction: str          # "BUY" or "SELL"
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

class CalculatorRequest(BaseModel):
    instrument: str
    direction: str          # "BUY" or "SELL"
    account_balance: float
    risk_pct: float = 1.0   # default 1%


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0",
        "session": signals.get_current_session(),
        "in_session": signals.is_trading_session(),
    }


@app.get("/api/account")
def account():
    try:
        summary   = oanda.get_account_summary()
        daily_pnl = oanda.get_daily_pnl()
        return {
            "account":   summary,
            "daily_pnl": daily_pnl,
        }
    except Exception as e:
        logger.error(f"/api/account error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals")
def get_signals():
    """Scan all 16 instruments. Returns signals with confluence scores."""
    try:
        results = signals.scan_all()
        fired = [r for r in results if r.get("signal") in ("BUY", "SELL")]
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
    """Live bid/ask for all 16 instruments."""
    results = {}
    for inst in signals.INSTRUMENTS:
        try:
            results[inst] = oanda.get_live_price(inst)
        except Exception as e:
            results[inst] = {"error": str(e)}
    return results


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


@app.post("/api/place-order")
def place_order(req: PlaceOrderRequest):
    """
    Place a market order.
    sl_price and tp_price must be ABSOLUTE PRICE LEVELS, not pip distances.
    Use /api/calculator first to get the correct levels.
    """
    try:
        units = req.units if req.direction == "BUY" else -req.units
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/breakeven")
def breakeven(req: BreakevenRequest):
    """
    Move stop loss to entry price (breakeven).
    YOUR RULE: trigger this when trade is +1R in profit.
    """
    try:
        result = oanda.move_to_breakeven(req.trade_id)
        return {"success": True, **result}
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
        # Get live price + ATR
        price_data = oanda.get_live_price(instrument)
        if not price_data:
            raise HTTPException(status_code=400, detail=f"Could not get price for {instrument}")

        candles = oanda.get_candles(instrument, "H4", count=20)
        if len(candles) < 15:
            raise HTTPException(status_code=400, detail="Insufficient candle data for ATR")

        import pandas as pd
        df  = pd.DataFrame(candles)
        hl  = df["high"] - df["low"]
        hc  = (df["high"] - df["close"].shift()).abs()
        lc  = (df["low"]  - df["close"].shift()).abs()
        atr = float(pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean().iloc[-1])

        entry = price_data["ask"] if direction == "BUY" else price_data["bid"]
        pip   = get_pip(instrument)
        dp    = price_decimals(instrument)

        levels = sl_tp_prices(entry, direction, atr, instrument)

        # Risk amount in account currency
        risk_amount = round(account_balance * risk_pct / 100, 2)

        # Units: risk_amount / (sl_distance_in_price_units * pip_value_per_unit)
        # For most CFDs on Oanda: 1 unit move = 1 pip value in quote currency
        sl_dist   = abs(entry - levels["sl"])
        units_raw = risk_amount / sl_dist if sl_dist > 0 else 0
        units     = max(1, int(units_raw))

        return {
            "instrument":    instrument,
            "direction":     direction,
            "entry":         round(entry, dp),
            "sl":            levels["sl"],
            "tp":            levels["tp"],
            "sl_pips":       levels["sl_pips"],
            "tp_pips":       levels["tp_pips"],
            "rr":            levels["rr"],
            "atr":           round(atr, dp),
            "pip":           pip,
            "units":         units,
            "risk_amount":   risk_amount,
            "risk_pct":      risk_pct,
            "account_balance": account_balance,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (for local dev: uvicorn api.main:app --reload)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
