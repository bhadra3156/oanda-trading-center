"""Oanda V20 API Client â€” clean version, no pip_utils dependency"""
import os, logging
from datetime import datetime
from dotenv import load_dotenv
from oandapyV20 import API
import oandapyV20.endpoints.accounts  as accounts
import oandapyV20.endpoints.orders    as orders
import oandapyV20.endpoints.trades    as trades
import oandapyV20.endpoints.positions as positions
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing   as pricing
from oandapyV20.contrib.requests import (
    MarketOrderRequest, TakeProfitDetails, StopLossDetails
)
load_dotenv()
logger = logging.getLogger(__name__)

class OandaClient:
    def __init__(self):
        self.api_key     = os.getenv("OANDA_API_KEY")
        self.account_id  = os.getenv("OANDA_ACCOUNT_ID")
        self.environment = os.getenv("OANDA_ENVIRONMENT", "live")
        if not self.api_key or not self.account_id:
            raise ValueError("Set OANDA_API_KEY and OANDA_ACCOUNT_ID in .env")
        self.client = API(access_token=self.api_key, environment=self.environment)
        logger.info(f"Oanda connected [{self.environment.upper()}]")

    def get_account_summary(self):
        r = accounts.AccountSummary(self.account_id)
        self.client.request(r)
        return r.response.get("account", {})

    def get_live_price(self, instrument):
        r = pricing.PricingInfo(accountID=self.account_id,
                                params={"instruments": instrument})
        self.client.request(r)
        prices = r.response.get("prices", [])
        if prices:
            p = prices[0]
            bid = float(p["bids"][0]["price"])
            ask = float(p["asks"][0]["price"])
            return {"instrument": instrument, "bid": bid, "ask": ask,
                    "mid": round((bid+ask)/2, 6),
                    "spread": round(ask-bid, 6), "time": p.get("time")}
        return {}

    def get_candles(self, instrument, granularity="H1", count=200):
        r = instruments.InstrumentsCandles(
            instrument=instrument,
            params={"count": count, "granularity": granularity})
        self.client.request(r)
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
                    "volume": int(c.get("volume", 0))
                })
        return result

    def get_open_trades(self):
        r = trades.OpenTrades(self.account_id)
        self.client.request(r)
        return r.response.get("trades", [])

    def get_trade_history(self, count=50):
        r = trades.TradesList(self.account_id,
                              params={"state": "CLOSED", "count": count})
        self.client.request(r)
        return r.response.get("trades", [])

    def close_trade(self, trade_id):
        r = trades.TradeClose(self.account_id, tradeID=str(trade_id))
        self.client.request(r)
        return r.response

    def place_order_with_levels(self, instrument, units, stop_loss, take_profit):
        data = MarketOrderRequest(instrument=instrument, units=int(units)).data
        data["order"]["stopLossOnFill"]   = StopLossDetails(
            price=round(float(stop_loss), 5)).data
        data["order"]["takeProfitOnFill"] = TakeProfitDetails(
            price=round(float(take_profit), 5)).data
        r = orders.OrderCreate(self.account_id, data=data)
        self.client.request(r)
        logger.info(f"Order placed: {instrument} {units} units")
        return r.response

    def get_open_positions(self):
        r = positions.OpenPositions(self.account_id)
        self.client.request(r)
        return r.response.get("positions", [])

    def get_daily_pnl(self):
        history = self.get_trade_history(count=100)
        today   = str(datetime.utcnow().date())
        pnl, wins, losses = 0.0, 0, 0
        for t in history:
            if t.get("closeTime", "")[:10] == today:
                pl = float(t.get("realizedPL", 0))
                pnl += pl
                wins   += 1 if pl > 0 else 0
                losses += 1 if pl <= 0 else 0
        count = wins + losses
        return {"date": today, "total_pnl": round(pnl, 2),
                "trade_count": count, "wins": wins, "losses": losses,
                "win_rate": round(wins/count*100, 1) if count else 0}