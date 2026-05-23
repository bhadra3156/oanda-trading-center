"""Oanda V20 API Client"""
import os, logging
from datetime import datetime
from dotenv import load_dotenv
from oandapyV20 import API
import oandapyV20.endpoints.accounts    as accounts
import oandapyV20.endpoints.orders      as orders
import oandapyV20.endpoints.trades      as trades
import oandapyV20.endpoints.positions   as positions
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.pricing     as pricing
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

    # ── ACCOUNT ───────────────────────────────────────────────────────────────
    def get_account_summary(self):
        r = accounts.AccountSummary(self.account_id)
        self.client.request(r)
        return r.response.get("account", {})

    def get_daily_pnl(self):
        """
        Returns daily + weekly P&L with full risk status.
        Limits:
          Daily soft warning:  5% loss
          Daily hard lockout:  8% loss
          Weekly soft warning: 10% loss
          Weekly hard lockout: 15% loss
          Margin minimum:      £50 absolute
        """
        history = self.get_trade_history(count=200)
        now     = datetime.utcnow()
        today   = str(now.date())

        # Week start = Monday
        week_start = str((now - __import__("datetime").timedelta(days=now.weekday())).date())

        balance = 0.0
        margin_avail = 0.0
        nav = 0.0
        try:
            acc          = self.get_account_summary()
            balance      = float(acc.get("balance", 0))
            margin_avail = float(acc.get("marginAvailable", 0))
            nav          = float(acc.get("NAV", balance))
        except Exception:
            pass

        daily_pnl, weekly_pnl = 0.0, 0.0
        wins, losses = 0, 0

        for t in history:
            close_date = t.get("closeTime", "")[:10]
            pl = float(t.get("realizedPL", 0))
            if close_date == today:
                daily_pnl += pl
                wins   += 1 if pl > 0 else 0
                losses += 1 if pl <= 0 else 0
            if close_date >= week_start:
                weekly_pnl += pl

        count = wins + losses

        # Loss amounts (positive number = loss)
        daily_loss  = abs(min(0.0, daily_pnl))
        weekly_loss = abs(min(0.0, weekly_pnl))

        # Thresholds
        daily_soft_limit  = round(balance * 0.05, 2)   # 5%
        daily_hard_limit  = round(balance * 0.08, 2)   # 8%
        weekly_soft_limit = round(balance * 0.10, 2)   # 10%
        weekly_hard_limit = round(balance * 0.15, 2)   # 15%
        margin_min        = 50.0                        # £50 absolute

        # Usage percentages
        daily_used_pct  = round(daily_loss  / daily_soft_limit  * 100, 1) if daily_soft_limit  else 0
        weekly_used_pct = round(weekly_loss / weekly_soft_limit * 100, 1) if weekly_soft_limit else 0

        # Risk status flags
        daily_warning  = daily_loss  >= daily_soft_limit
        daily_lockout  = daily_loss  >= daily_hard_limit
        weekly_warning = weekly_loss >= weekly_soft_limit
        weekly_lockout = weekly_loss >= weekly_hard_limit
        margin_warning = margin_avail < margin_min
        any_lockout    = daily_lockout or weekly_lockout

        return {
            # Daily
            "date":              today,
            "total_pnl":         round(daily_pnl, 2),
            "daily_pnl":         round(daily_pnl, 2),
            "daily_loss":        round(daily_loss, 2),
            "daily_soft_limit":  daily_soft_limit,
            "daily_hard_limit":  daily_hard_limit,
            "daily_used_pct":    daily_used_pct,
            "daily_warning":     daily_warning,
            "daily_lockout":     daily_lockout,
            # Weekly
            "week_start":        week_start,
            "weekly_pnl":        round(weekly_pnl, 2),
            "weekly_loss":       round(weekly_loss, 2),
            "weekly_soft_limit": weekly_soft_limit,
            "weekly_hard_limit": weekly_hard_limit,
            "weekly_used_pct":   weekly_used_pct,
            "weekly_warning":    weekly_warning,
            "weekly_lockout":    weekly_lockout,
            # Margin
            "margin_available":  round(margin_avail, 2),
            "margin_warning":    margin_warning,
            "margin_min":        margin_min,
            # Overall
            "any_lockout":       any_lockout,
            "daily_breached":    daily_lockout,
            # Trade stats
            "trade_count":       count,
            "wins":              wins,
            "losses":            losses,
            "win_rate":          round(wins / count * 100, 1) if count else 0,
            "balance":           round(balance, 2),
        }

    # ── PRICING ───────────────────────────────────────────────────────────────
    def get_live_price(self, instrument):
        r = pricing.PricingInfo(
            accountID=self.account_id,
            params={"instruments": instrument}
        )
        self.client.request(r)
        prices = r.response.get("prices", [])
        if prices:
            p   = prices[0]
            bid = float(p["bids"][0]["price"])
            ask = float(p["asks"][0]["price"])
            return {
                "instrument": instrument,
                "bid":    bid,
                "ask":    ask,
                "mid":    round((bid + ask) / 2, 6),
                "spread": round(ask - bid, 6),
                "time":   p.get("time"),
            }
        return {}

    # ── CANDLES ───────────────────────────────────────────────────────────────
    def get_candles(self, instrument, granularity="H4", count=200):
        r = instruments.InstrumentsCandles(
            instrument=instrument,
            params={"count": count, "granularity": granularity}
        )
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
                    "volume": int(c.get("volume", 0)),
                })
        return result

    # ── TRADES ────────────────────────────────────────────────────────────────
    def get_open_trades(self):
        r = trades.OpenTrades(self.account_id)
        self.client.request(r)
        return r.response.get("trades", [])

    def get_trade_history(self, count=50):
        r = trades.TradesList(
            self.account_id,
            params={"state": "CLOSED", "count": count}
        )
        self.client.request(r)
        return r.response.get("trades", [])

    def close_trade(self, trade_id):
        r = trades.TradeClose(self.account_id, tradeID=str(trade_id))
        self.client.request(r)
        return r.response

    def close_trade_partial(self, trade_id, units):
        """Close a partial number of units on an open trade."""
        r = trades.TradeClose(
            self.account_id,
            tradeID=str(trade_id),
            data={"units": str(abs(int(units)))}
        )
        self.client.request(r)
        return r.response

    def modify_trade_sl(self, trade_id, new_sl_price):
        """
        Move stop loss to a new price level.
        Used for Move to Breakeven and Trail ATR.
        """
        import oandapyV20.endpoints.trades as trades_ep
        data = {
            "stopLoss": {
                "price":       str(round(float(new_sl_price), 5)),
                "timeInForce": "GTC",
            }
        }
        r = trades_ep.TradeClientExtensions(
            self.account_id,
            tradeID=str(trade_id)
        )
        # Use the correct endpoint for modifying orders
        import oandapyV20.endpoints.orders as orders_ep
        # Get current trade to find the stopLoss order ID
        trade_r = trades_ep.TradeDetails(self.account_id, tradeID=str(trade_id))
        self.client.request(trade_r)
        trade_data = trade_r.response.get("trade", {})
        sl_order   = trade_data.get("stopLossOrder", {})
        sl_id      = sl_order.get("id")

        if sl_id:
            # Modify existing SL order
            modify_data = {
                "order": {
                    "type":        "STOP_LOSS",
                    "tradeID":     str(trade_id),
                    "price":       str(round(float(new_sl_price), 5)),
                    "timeInForce": "GTC",
                }
            }
            r2 = orders_ep.OrderReplace(
                self.account_id,
                orderID=str(sl_id),
                data=modify_data
            )
            self.client.request(r2)
            return r2.response
        else:
            # Create new SL order
            create_data = {
                "order": {
                    "type":        "STOP_LOSS",
                    "tradeID":     str(trade_id),
                    "price":       str(round(float(new_sl_price), 5)),
                    "timeInForce": "GTC",
                }
            }
            r3 = orders_ep.OrderCreate(self.account_id, data=create_data)
            self.client.request(r3)
            return r3.response

    # ── ORDERS ────────────────────────────────────────────────────────────────
    def place_order_with_levels(self, instrument, units, stop_loss, take_profit):
        data = MarketOrderRequest(instrument=instrument, units=int(units)).data
        data["order"]["stopLossOnFill"]   = StopLossDetails(
            price=round(float(stop_loss), 5)
        ).data
        data["order"]["takeProfitOnFill"] = TakeProfitDetails(
            price=round(float(take_profit), 5)
        ).data
        r = orders.OrderCreate(self.account_id, data=data)
        self.client.request(r)
        logger.info(f"Order placed: {instrument} {units} units")
        return r.response

    # ── POSITIONS ────────────────────────────────────────────────────────────
    def get_open_positions(self):
        r = positions.OpenPositions(self.account_id)
        self.client.request(r)
        return r.response.get("positions", [])