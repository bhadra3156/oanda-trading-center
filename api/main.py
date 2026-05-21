"""
Oanda Trading Center — FastAPI Backend v2
Runs on Render.com (free)
All endpoints for the trading dashboard
"""

import os
import logging
from datetime import datetime
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

load_dotenv()

from api.oanda           import OandaClient
from api.signals         import SignalEngine
from api.ai              import GeminiAnalyst
from api.telegram        import TelegramBot
from api.supabase_client import SupabaseClient
from api.calculator      import PositionCalculator
from api.news_check      import get_all_upcoming_events, check_news_blackout

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
    allow_methods=["*"],
    allow_headers=["*"],
)

oanda    = OandaClient()
signals  = SignalEngine(oanda)
gemini   = GeminiAnalyst()
telegram = TelegramBot()
db       = SupabaseClient()
calc     = PositionCalculator(oanda)

_alerted = set()


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


@app.get("/")
@app.get("/health")
async def health():
    return {
        "status":      "running",
        "service":     "Oanda Trading Center",
        "version":     "2.0.0",
        "timestamp":   datetime.utcnow().isoformat(),
        "environment": os.getenv("OANDA_ENVIRONMENT", "live").upper()
    }


@app.get("/api/account")
async def get_account():
    try:
        summary = oanda.get_account_summary()
        pnl     = oanda.get_daily_pnl()
        return {"ok": True, "account": summary, "pnl": pnl}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/prices")
async def get_prices():
    instruments = [
        "EUR_USD","GBP_JPY","XAU_USD","XAG_USD",
        "NATGAS_USD","WTICO_USD","CORN_USD","SUGAR_USD",
        "WHEAT_USD","SOYBN_USD","SPX500_USD","NAS100_USD",
        "UK100_GBP","DE30_EUR","XPD_USD"
    ]
    prices = {}
    for inst in instruments:
        try:
            prices[inst] = oanda.get_live_price(inst)
        except Exception:
            pass
    return {"ok": True, "prices": prices, "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/signals")
async def get_signals():
    try:
        instruments = [
            "EUR_USD","GBP_JPY","XAU_USD","XAG_USD",
            "NATGAS_USD","WTICO_USD","CORN_USD","SUGAR_USD",
            "WHEAT_USD","SOYBN_USD","SPX500_USD","NAS100_USD",
            "UK100_GBP","DE30_EUR","XPD_USD"
        ]
        results = {}
        for inst in instruments:
            logger.info(f"Analysing {inst}...")
            try:
                sig = signals.analyse(inst)
            except Exception as e:
                sig = {"instrument":inst,"signal":"ERROR","error":str(e),"reasons":[str(e)],"confidence":0}

            if sig.get("signal") in ("BUY","SELL"):
                logger.info(f"AI analysing {inst}...")
                try:
                    ai = gemini.analyse(inst, sig)
                    sig["ai"] = ai
                except Exception:
                    sig["ai"] = None
                try:
                    db.save_signal(inst, sig)
                except Exception as e:
                    logger.error(f"Supabase: {e}")
                alert_key = f"{inst}_{sig['signal']}_{sig.get('price','')}"
                if alert_key not in _alerted:
                    try:
                        telegram.send_signal(inst, sig, sig.get("ai") or {})
                        _alerted.add(alert_key)
                        if len(_alerted) > 200:
                            _alerted.clear()
                    except Exception as e:
                        logger.error(f"Telegram: {e}")
            else:
                sig["ai"] = None
            results[inst] = sig
        return {"ok":True,"signals":results,"timestamp":datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/data")
async def get_dashboard_data():
    try:
        summary     = oanda.get_account_summary()
        open_trades = oanda.get_open_trades()
        pnl         = oanda.get_daily_pnl()
        instruments = [
            "EUR_USD","GBP_JPY","XAU_USD","XAG_USD",
            "NATGAS_USD","WTICO_USD","CORN_USD","SUGAR_USD",
            "WHEAT_USD","SOYBN_USD","SPX500_USD","NAS100_USD",
            "UK100_GBP","DE30_EUR","XPD_USD"
        ]
        sig_results, prices = {}, {}
        open_positions_for_corr = [
            {"instrument":t.get("instrument"),"direction":"BUY" if float(t.get("currentUnits",0))>0 else "SELL"}
            for t in open_trades
        ]
        for inst in instruments:
            logger.info(f"Analysing {inst}...")
            try:
                sig = signals.analyse(inst)
            except Exception as e:
                sig = {"instrument":inst,"signal":"ERROR","error":str(e),"reasons":[str(e)],"confidence":0}
            if sig.get("signal") in ("BUY","SELL"):
                logger.info(f"AI analysing {inst}...")
                try:
                    ai = gemini.analyse(inst, sig)
                    sig["ai"] = ai
                except Exception:
                    sig["ai"] = None
                if CORRELATION_AVAILABLE:
                    try:
                        sig["correlation"] = check_correlation(
                            new_instrument=inst,
                            new_direction=sig["signal"],
                            open_positions=open_positions_for_corr,
                        )
                    except Exception as e:
                        logger.error(f"Correlation {inst}: {e}")
                        sig["correlation"] = {"safe":True,"warnings":[],"block_trade":False,"summary":""}
                else:
                    sig["correlation"] = {"safe":True,"warnings":[],"block_trade":False,"summary":""}
                try:
                    db.save_signal(inst, sig)
                except Exception:
                    pass
                alert_key = f"{inst}_{sig['signal']}_{sig.get('price','')}"
                if alert_key not in _alerted:
                    try:
                        telegram.send_signal(inst, sig, sig.get("ai") or {})
                        _alerted.add(alert_key)
                    except Exception:
                        pass
            else:
                sig["ai"] = None
                sig["correlation"] = {"safe":True,"warnings":[],"block_trade":False,"summary":""}
            sig_results[inst] = sig
            try:
                prices[inst] = oanda.get_live_price(inst)
            except Exception:
                pass
        trades_data = [{
            "id":t.get("id"),"instrument":t.get("instrument"),
            "units":float(t.get("currentUnits",0)),"entry":t.get("price"),
            "pnl":float(t.get("unrealizedPL",0)),
            "sl":t.get("stopLossOrder",{}).get("price","—"),
            "tp":t.get("takeProfitOrder",{}).get("price","—"),
            "direction":"LONG" if float(t.get("currentUnits",0))>0 else "SHORT",
        } for t in open_trades]
        try:
            db.save_account_snapshot(summary)
        except Exception:
            pass
        return {
            "ok":True,"timestamp":datetime.utcnow().isoformat(),
            "environment":os.getenv("OANDA_ENVIRONMENT","live").upper(),
            "account":{
                "balance":float(summary.get("balance",0)),
                "nav":float(summary.get("NAV",0)),
                "unrealizedPL":float(summary.get("unrealizedPL",0)),
                "marginUsed":float(summary.get("marginUsed",0)),
                "marginAvailable":float(summary.get("marginAvailable",0)),
                "openTradeCount":int(summary.get("openTradeCount",0)),
                "currency":summary.get("currency","USD"),
            },
            "pnl":pnl,"trades":trades_data,"prices":prices,"signals":sig_results,
        }
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
async def get_trades():
    try:
        trades = oanda.get_open_trades()
        return {"ok":True,"trades":trades}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/place-order")
async def place_order(order: OrderRequest):
    try:
        units  = order.units if order.direction=="BUY" else -order.units
        result = oanda.place_order_with_levels(
            instrument=order.instrument, units=units,
            stop_loss=order.stop_loss, take_profit=order.take_profit
        )
        try:
            db.save_trade({"instrument":order.instrument,"signal":order.direction,
                           "units":order.units,"stop_loss":order.stop_loss,"take_profit":order.take_profit})
        except Exception:
            pass
        try:
            price = oanda.get_live_price(order.instrument)
            entry = price.get("ask" if order.direction=="BUY" else "bid",0)
            telegram.send_trade_confirmation(order.instrument,order.direction,entry,order.stop_loss,order.take_profit,order.units)
        except Exception:
            pass
        return {"ok":True,"result":result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/close-trade")
async def close_trade(req: CloseRequest):
    try:
        result = oanda.close_trade(req.trade_id)
        return {"ok":True,"result":result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calculator")
async def calculate_position(req: CalculatorRequest):
    try:
        result = calc.calculate(req.instrument, req.direction, req.risk_percent)
        return {"ok":True,"calculation":result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/journal")
async def get_journal():
    try:
        stats = db.get_journal_stats()
        return {"ok":True,"stats":stats}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/news")
async def get_news():
    try:
        events = get_all_upcoming_events(hours=24)
        return {"ok":True,"news":events,"count":len(events)}
    except Exception as e:
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        weekday = now.weekday()
        fallback = [
            {"title":"US Economic Data Window","country":"USD","impact":"High",
             "time_utc":f"{today} 13:30 UTC",
             "minutes":max(1,int((13.5-now.hour-now.minute/60)*60)),
             "in_blackout":False,"source":"fallback"},
        ]
        if weekday==2:
            fallback.append({"title":"EIA Oil Inventories","country":"USD","impact":"High",
                "time_utc":f"{today} 15:30 UTC",
                "minutes":max(1,int((15.5-now.hour-now.minute/60)*60)),
                "in_blackout":False,"source":"fallback"})
        return {"ok":True,"news":fallback,"error":str(e),"source":"fallback"}


@app.get("/api/news-check/{instrument}")
async def news_check_instrument(instrument: str):
    try:
        result = check_news_blackout(instrument)
        return {"ok":True,"result":result}
    except Exception as e:
        return {"ok":False,"error":str(e)}


@app.post("/api/check-correlation")
async def check_correlation_endpoint(request: CorrelationCheckRequest):
    if not CORRELATION_AVAILABLE:
        return {"safe":True,"warnings":[],"block_trade":False,"summary":""}
    positions_dicts = [{"instrument":p.instrument,"direction":p.direction} for p in request.open_positions]
    result = check_correlation(
        new_instrument=request.new_instrument,
        new_direction=request.new_direction,
        open_positions=positions_dicts,
    )
    return result


@app.get("/api/correlation-map/{instrument}")
async def correlation_map_endpoint(instrument: str):
    if not CORRELATION_AVAILABLE:
        return {"instrument":instrument,"correlations":[]}
    return {"instrument":instrument,"correlations":get_correlation_map_for_instrument(instrument)}


@app.post("/api/ai-debrief")
async def ai_debrief(req: DebriefRequest):
    try:
        analysis = gemini.analyse_debrief(req.prompt)
        return {"ok":True,"analysis":analysis}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api.main:app", host="0.0.0.0", port=port, reload=False)