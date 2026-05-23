"""
Oanda Trading Center — FastAPI Backend v2
8 instruments: GBP/JPY, EUR/USD, XAU/USD, SUGAR, WHEAT, SPX500, WTI, NATGAS
Mode 1 Semi-Auto Trading: Signal → Telegram YES/NO → Execute
"""
import os, asyncio, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

load_dotenv()

from api.oanda           import OandaClient
from api.signals         import SignalEngine
from api.ai              import GeminiAnalyst
from api.telegram        import TelegramBot
from api.supabase_client import SupabaseClient
from api.calculator      import PositionCalculator
from api.news_check      import get_all_upcoming_events, check_news_blackout
from api.backtest        import run_backtest
from api.auto_trader     import (
    check_new_signals, safety_check, add_pending_trade,
    check_expired_trades, process_telegram_reply,
    calculate_units, get_all_pending, clear_pending_trade,
    _last_update_id
)

try:
    from api.correlation import check_correlation, get_correlation_map_for_instrument
    CORRELATION_AVAILABLE = True
except ImportError:
    CORRELATION_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Oanda Trading Center", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    allow_credentials=False,
    max_age=86400,
)

oanda    = OandaClient()
signals  = SignalEngine(oanda)
gemini   = GeminiAnalyst()
telegram = TelegramBot()
db       = SupabaseClient()
calc     = PositionCalculator(oanda)

_alerted  = set()
_executor = ThreadPoolExecutor(max_workers=8)

# ── 8 instruments only ────────────────────────────────────────────────────────
INSTRUMENTS = [
    "GBP_JPY",
    "EUR_USD",
    "XAU_USD",
    "SUGAR_USD",
    "WHEAT_USD",
    "SPX500_USD",
    "WTICO_USD",
    "NATGAS_USD",
]

# ── Auto-trader state ─────────────────────────────────────────────────────────
_auto_trader_running = False
_last_tg_update_id   = 0

# ── MODELS ────────────────────────────────────────────────────────────────────
class OrderRequest(BaseModel):
    instrument:  str
    direction:   str
    units:       int
    stop_loss:   float
    take_profit: float

class CalculatorRequest(BaseModel):
    instrument:   str
    direction:    str
    risk_percent: float = 1.0

class CloseRequest(BaseModel):
    trade_id: str
    units:    Optional[int] = None

class ModifySlRequest(BaseModel):
    trade_id:     str
    new_sl_price: float

class OpenPosition(BaseModel):
    instrument: str
    direction:  str

class CorrelationCheckRequest(BaseModel):
    new_instrument: str
    new_direction:  str
    open_positions: List[OpenPosition]

class DebriefRequest(BaseModel):
    trades: list
    prompt: str


# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.get("/")
@app.get("/health")
@app.get("/api/health")
async def health():
    now  = datetime.utcnow()
    hour = now.hour
    if 7 <= hour < 17:
        session, in_session = "London",   True
    elif 13 <= hour < 22:
        session, in_session = "New York", True
    else:
        session, in_session = "Asian",    False
    return {
        "status":           "ok",
        "time":             now.isoformat() + "+00:00",
        "version":          "2.0.0",
        "session":          session,
        "in_session":       in_session,
        "instruments":      len(INSTRUMENTS),
        "auto_trader":      "running" if _auto_trader_running else "stopped",
        "pending_trades":   len(get_all_pending()),
    }


# ── ACCOUNT ───────────────────────────────────────────────────────────────────
@app.get("/api/account")
async def get_account():
    try:
        summary = oanda.get_account_summary()
        pnl     = oanda.get_daily_pnl()
        return {"ok": True, "account": summary, "pnl": pnl}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── PRICES ────────────────────────────────────────────────────────────────────
@app.get("/api/prices")
async def get_prices():
    prices = {}
    for inst in INSTRUMENTS:
        try:
            prices[inst] = oanda.get_live_price(inst)
        except Exception:
            pass
    return {"ok": True, "prices": prices, "timestamp": datetime.utcnow().isoformat()}


# ── TRADES ────────────────────────────────────────────────────────────────────
@app.get("/api/trades")
async def get_trades():
    try:
        trades = oanda.get_open_trades()
        return {"ok": True, "trades": trades}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── ANALYSE ONE INSTRUMENT ────────────────────────────────────────────────────
def _analyse_one(inst: str) -> dict:
    try:
        sig = signals.analyse(inst)
    except Exception as e:
        return {
            "instrument": inst, "signal": "ERROR",
            "error": str(e), "reasons": [str(e)],
            "confidence": 0, "price": 0, "ai": None,
        }

    if sig.get("signal") in ("BUY", "SELL"):
        try:
            sig["ai"] = gemini.analyse(inst, sig)
        except Exception as e:
            sig["ai"] = None

        try:
            db.save_signal(inst, sig)
        except Exception as e:
            logger.error(f"Supabase save error: {e}")

        # Standard Telegram alert (non-auto)
        alert_key = f"{inst}_{sig['signal']}_{sig.get('price','')}"
        if alert_key not in _alerted:
            try:
                telegram.send_signal(inst, sig, sig.get("ai") or {})
                _alerted.add(alert_key)
                if len(_alerted) > 100:
                    _alerted.clear()
            except Exception:
                pass
    else:
        sig["ai"] = None

    return sig


# ── SIGNALS ───────────────────────────────────────────────────────────────────
@app.get("/api/signals")
async def get_signals():
    try:
        loop  = asyncio.get_running_loop()
        tasks = [
            loop.run_in_executor(_executor, _analyse_one, inst)
            for inst in INSTRUMENTS
        ]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for inst, result in zip(INSTRUMENTS, results_list):
            if isinstance(result, Exception):
                results[inst] = {
                    "instrument": inst, "signal": "ERROR",
                    "error": str(result), "confidence": 0,
                    "price": 0, "ai": None,
                }
            else:
                results[inst] = result

        # ── AUTO-TRADER: process new signals ─────────────────────────────────
        try:
            new_sigs = check_new_signals(results)
            for sig in new_sigs:
                await asyncio.get_running_loop().run_in_executor(
                    _executor, _process_auto_signal, sig
                )
        except Exception as e:
            logger.error(f"Auto-trader signal processing error: {e}")

        # Save daily account snapshot (once per scan)
        try:
            hour = datetime.utcnow().hour
            if hour % 4 == 0:  # Save every 4 hours
                summary = oanda.get_account_summary()
                db.save_account_snapshot(summary)
        except Exception as e:
            logger.debug(f"Snapshot save skipped: {e}")

        return {"ok": True, "signals": results, "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _process_auto_signal(sig: dict):
    """
    Called when a NEW signal is detected.
    Runs safety checks, sends Telegram alert if passed.
    """
    inst       = sig.get("instrument", "")
    direction  = sig.get("signal", "")
    confidence = sig.get("confidence", 0)

    logger.info(f"Auto-trader: processing NEW signal {inst} {direction} ({confidence}%)")

    # Check news blackout first — notify with details if blocked
    try:
        news = check_news_blackout(inst)
        if news.get("in_blackout"):
            telegram.send_news_block(inst, sig, news)
            logger.info(f"Auto-trader: {inst} blocked by news blackout")
            return
    except Exception as e:
        logger.error(f"News check error in auto-trader: {e}")

    # Run safety checks
    corr_checker = check_correlation if CORRELATION_AVAILABLE else None
    passed, reason = safety_check(sig, oanda, corr_checker)

    if not passed:
        # Only notify for important blocks (not just low confidence)
        if "confidence" not in reason.lower():
            telegram.send_trade_blocked(inst, direction, reason)
        logger.info(f"Auto-trader: {inst} blocked — {reason}")
        return

    # Calculate position size
    try:
        account = oanda.get_account_summary()
        balance = float(account.get("balance", 0))
        atr     = sig.get("atr", 0)
        units   = calculate_units(balance, atr, sig)

        if units <= 0:
            logger.warning(f"Auto-trader: {inst} — calculated 0 units, skipping")
            return

        # Store as pending trade
        add_pending_trade(sig, units)

        # Send Telegram alert asking for YES/NO
        ai = sig.get("ai") or {}
        telegram.send_signal_alert(inst, sig, ai, units)
        logger.info(f"Auto-trader: alert sent for {inst} {direction} {units} units")

    except Exception as e:
        logger.error(f"Auto-trader signal processing error: {e}")


# ── BACKGROUND TASK: Poll Telegram for replies ────────────────────────────────
async def telegram_polling_loop():
    """
    Runs forever in background.
    Checks Telegram every 30 seconds for YES/NO replies.
    Also clears expired pending trades.
    """
    global _auto_trader_running, _last_tg_update_id
    _auto_trader_running = True
    logger.info("Auto-trader polling loop started")

    corr_checker = check_correlation if CORRELATION_AVAILABLE else None

    while True:
        try:
            # 1. Check for expired pending trades
            expired = check_expired_trades()
            for trade in expired:
                telegram.send_expired(trade)
                logger.info(f"Expired trade notified: {trade.get('instrument')}")

            # 2. Poll Telegram for new messages
            updates = telegram.get_updates(offset=_last_tg_update_id + 1)

            for update in updates:
                _last_tg_update_id = update.get("update_id", _last_tg_update_id)
                message = update.get("message", {})
                text    = message.get("text", "").strip()

                if not text:
                    continue

                # Only process messages from YOUR chat ID
                from_id = str(message.get("chat", {}).get("id", ""))
                if from_id != str(telegram.chat_id):
                    logger.warning(f"Message from unknown chat {from_id} — ignored")
                    continue

                logger.info(f"Telegram reply received: '{text}'")

                # Handle STOP/RESUME specially
                text_upper = text.upper()
                if text_upper == "STOP" or text_upper == "PAUSE":
                    telegram.pause()
                    telegram.send("*Auto-trader paused.*\nSend RESUME to restart.")
                    continue
                elif text_upper == "RESUME":
                    telegram.resume()
                    telegram.send("*Auto-trader resumed.*\nWatching for signals.")
                    continue

                # Process YES/NO/STATUS
                process_telegram_reply(
                    text, oanda, telegram, corr_checker
                )

        except Exception as e:
            logger.error(f"Telegram polling error: {e}")

        await asyncio.sleep(30)


# ── STARTUP: Launch background tasks ─────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telegram_polling_loop())
    logger.info("Auto-trader background task launched")


# ── PLACE ORDER (manual) ──────────────────────────────────────────────────────
@app.post("/api/place-order")
async def place_order(order: OrderRequest):
    try:
        units  = order.units if order.direction == "BUY" else -order.units
        result = oanda.place_order_with_levels(
            instrument=order.instrument,
            units=units,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
        )
        try:
            db.save_trade({
                "instrument": order.instrument,
                "signal":     order.direction,
                "units":      order.units,
                "stop_loss":  order.stop_loss,
                "take_profit":order.take_profit,
            })
        except Exception:
            pass
        try:
            price = oanda.get_live_price(order.instrument)
            entry = price.get("ask" if order.direction == "BUY" else "bid", 0)
            telegram.send_trade_confirmation(
                order.instrument, order.direction, entry,
                order.stop_loss, order.take_profit, order.units,
            )
        except Exception:
            pass
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── CLOSE TRADE ───────────────────────────────────────────────────────────────
@app.post("/api/close-trade")
async def close_trade(req: CloseRequest):
    try:
        if req.units is not None:
            result = oanda.close_trade_partial(req.trade_id, req.units)
        else:
            result = oanda.close_trade(req.trade_id)
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── MODIFY STOP LOSS ──────────────────────────────────────────────────────────
@app.post("/api/modify-sl")
async def modify_sl(req: ModifySlRequest):
    try:
        result = oanda.modify_trade_sl(req.trade_id, req.new_sl_price)
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── CALCULATOR ────────────────────────────────────────────────────────────────
@app.post("/api/calculator")
async def calculate_position(req: CalculatorRequest):
    try:
        result = calc.calculate(req.instrument, req.direction, req.risk_percent)
        return {"ok": True, "calculation": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── CANDLES ───────────────────────────────────────────────────────────────────
@app.get("/api/candles/{instrument}")
async def get_candles(instrument: str, granularity: str = "H4", count: int = 60):
    try:
        candles = oanda.get_candles(instrument, granularity=granularity, count=count)
        return {"ok": True, "candles": candles, "instrument": instrument}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── PERFORMANCE ──────────────────────────────────────────────────────────────
@app.get("/api/performance")
async def get_performance():
    try:
        stats = db.get_performance_stats()
        # Add live account data
        try:
            acc   = oanda.get_account_summary()
            pnl   = oanda.get_daily_pnl()
            stats["live_balance"] = float(acc.get("balance", 0))
            stats["live_nav"]     = float(acc.get("NAV", 0))
            stats["live_upl"]     = float(acc.get("unrealizedPL", 0))
            stats["daily_pnl"]    = pnl.get("daily_pnl", 0)
            stats["open_trades"]  = int(acc.get("openTradeCount", 0))
        except Exception:
            pass
        return {"ok": True, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── JOURNAL ───────────────────────────────────────────────────────────────────
@app.get("/api/journal")
async def get_journal():
    try:
        stats = db.get_journal_stats()
        return {"ok": True, "stats": stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── RISK STATUS ──────────────────────────────────────────────────────────────
@app.get("/api/risk-status")
async def get_risk_status():
    """
    Returns full drawdown protection status.
    Frontend uses this to enforce hard trade blocks.
    """
    try:
        pnl     = oanda.get_daily_pnl()
        trades  = oanda.get_open_trades()
        account = oanda.get_account_summary()
        return {
            "ok":              True,
            "daily_pnl":       pnl.get("daily_pnl", 0),
            "daily_loss":      pnl.get("daily_loss", 0),
            "daily_used_pct":  pnl.get("daily_used_pct", 0),
            "daily_warning":   pnl.get("daily_warning", False),
            "daily_lockout":   pnl.get("daily_lockout", False),
            "daily_soft_limit":pnl.get("daily_soft_limit", 0),
            "daily_hard_limit":pnl.get("daily_hard_limit", 0),
            "weekly_pnl":      pnl.get("weekly_pnl", 0),
            "weekly_loss":     pnl.get("weekly_loss", 0),
            "weekly_used_pct": pnl.get("weekly_used_pct", 0),
            "weekly_warning":  pnl.get("weekly_warning", False),
            "weekly_lockout":  pnl.get("weekly_lockout", False),
            "weekly_soft_limit":pnl.get("weekly_soft_limit", 0),
            "weekly_hard_limit":pnl.get("weekly_hard_limit", 0),
            "margin_available":pnl.get("margin_available", 0),
            "margin_warning":  pnl.get("margin_warning", False),
            "margin_min":      pnl.get("margin_min", 50),
            "any_lockout":     pnl.get("any_lockout", False),
            "open_trades":     len(trades),
            "max_trades":      3,
            "trades_full":     len(trades) >= 3,
            "balance":         pnl.get("balance", 0),
        }
    except Exception as e:
        logger.error(f"Risk status error: {e}")
        return {"ok": False, "error": str(e), "any_lockout": False}


# ── NEWS ──────────────────────────────────────────────────────────────────────
@app.get("/api/news")
async def get_news():
    try:
        events = get_all_upcoming_events(hours=24)
        return {"ok": True, "news": events, "count": len(events)}
    except Exception as e:
        now   = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        return {
            "ok":    True,
            "news":  [{"title": "US Economic Data", "country": "USD",
                       "date": f"{today} 13:30 UTC", "impact": "High"}],
            "error": str(e), "source": "fallback",
        }


# ── AUTO-TRADER STATUS ────────────────────────────────────────────────────────
@app.get("/api/auto-trader/status")
async def auto_trader_status():
    return {
        "ok":             True,
        "running":        _auto_trader_running,
        "paused":         telegram.is_paused,
        "pending_trades": get_all_pending(),
        "last_update_id": _last_tg_update_id,
    }


# ── NEWS CHECK ────────────────────────────────────────────────────────────────
@app.get("/api/news-check/{instrument}")
async def news_check_instrument(instrument: str):
    try:
        result = check_news_blackout(instrument)
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── CORRELATION ───────────────────────────────────────────────────────────────
@app.post("/api/check-correlation")
async def check_correlation_endpoint(request: CorrelationCheckRequest):
    if not CORRELATION_AVAILABLE:
        return {"safe": True, "warnings": [], "block_trade": False, "summary": ""}
    positions_dicts = [
        {"instrument": p.instrument, "direction": p.direction}
        for p in request.open_positions
    ]
    return check_correlation(
        new_instrument=request.new_instrument,
        new_direction=request.new_direction,
        open_positions=positions_dicts,
    )


@app.get("/api/correlation-map/{instrument}")
async def correlation_map_endpoint(instrument: str):
    if not CORRELATION_AVAILABLE:
        return {"instrument": instrument, "correlations": []}
    return {
        "instrument":   instrument,
        "correlations": get_correlation_map_for_instrument(instrument),
    }


# ── BACKTEST ──────────────────────────────────────────────────────────────────
class BacktestRequest(BaseModel):
    instruments:      list
    starting_balance: float = 627.0
    risk_pct:         float = 1.0
    max_hold_bars:    int   = 20
    signal_threshold: int   = 55
    candle_count:     int   = 5000  # ~2.3 years of H4

@app.post("/api/backtest")
async def run_backtest_endpoint(req: BacktestRequest):
    """
    Run H4 confluence backtest on selected instruments.
    Fetches historical candles from Oanda and runs the exact
    same scoring logic as the live signal engine.
    """
    try:
        if not req.instruments:
            raise HTTPException(status_code=400, detail="Select at least one instrument")
        if len(req.instruments) > 8:
            raise HTTPException(status_code=400, detail="Max 8 instruments")

        results = {}
        loop    = asyncio.get_running_loop()

        async def backtest_one(instrument: str):
            try:
                # Fetch historical candles
                candles = await loop.run_in_executor(
                    _executor,
                    lambda: oanda.get_candles(
                        instrument,
                        granularity="H4",
                        count=req.candle_count
                    )
                )
                if not candles:
                    return instrument, {"error": "No candle data returned", "instrument": instrument}

                # Run backtest
                result = await loop.run_in_executor(
                    _executor,
                    lambda: run_backtest(
                        instrument=instrument,
                        candles=candles,
                        starting_balance=req.starting_balance,
                        risk_pct=req.risk_pct,
                        max_hold_bars=req.max_hold_bars,
                        signal_threshold=req.signal_threshold,
                    )
                )
                return instrument, result
            except Exception as e:
                logger.error(f"Backtest error {instrument}: {e}")
                return instrument, {"error": str(e), "instrument": instrument}

        # Run all instruments concurrently
        tasks = [backtest_one(inst) for inst in req.instruments]
        pairs = await asyncio.gather(*tasks, return_exceptions=True)

        for pair in pairs:
            if isinstance(pair, Exception):
                continue
            inst, result = pair
            results[inst] = result

        # Aggregate summary across all instruments
        all_trades = []
        for r in results.values():
            if isinstance(r, dict) and "trades" in r:
                all_trades.extend(r.get("trades", []))

        total_wins   = sum(1 for t in all_trades if t["outcome"] == "WIN")
        total_losses = sum(1 for t in all_trades if t["outcome"] == "LOSS")
        total_count  = len(all_trades)
        overall_wr   = round(total_wins/total_count*100, 1) if total_count else 0
        overall_rs   = [t["r_multiple"] for t in all_trades]
        overall_avgr = round(sum(overall_rs)/len(overall_rs), 3) if overall_rs else 0

        return {
            "ok":              True,
            "results":         results,
            "summary": {
                "total_trades":  total_count,
                "total_wins":    total_wins,
                "total_losses":  total_losses,
                "overall_win_rate": overall_wr,
                "overall_avg_r":    overall_avgr,
                "instruments_run":  len(results),
            },
            "params": {
                "starting_balance": req.starting_balance,
                "risk_pct":         req.risk_pct,
                "max_hold_bars":    req.max_hold_bars,
                "signal_threshold": req.signal_threshold,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Backtest endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── AI DEBRIEF ────────────────────────────────────────────────────────────────
@app.post("/api/ai-debrief")
async def ai_debrief(req: DebriefRequest):
    try:
        analysis = gemini.analyse_debrief(req.prompt)
        return {"ok": True, "analysis": analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)