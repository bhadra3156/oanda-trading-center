"""
Position Size Calculator
Calculates exact trade size based on account balance and risk percentage.
Never risk more than 5% of account.
"""
import logging
logger = logging.getLogger(__name__)

PIP_VALUES = {
    "JPY":    {"pip": 0.01,    "pip_val_per_unit": 0.000091},
    "XAU":    {"pip": 0.1,     "pip_val_per_unit": 0.1},
    "XAG":    {"pip": 0.01,    "pip_val_per_unit": 0.01},
    "XPD":    {"pip": 0.1,     "pip_val_per_unit": 0.1},
    "BCO":    {"pip": 0.01,    "pip_val_per_unit": 0.01},
    "WTICO":  {"pip": 0.01,    "pip_val_per_unit": 0.01},
    "NATGAS": {"pip": 0.001,   "pip_val_per_unit": 0.001},
    "CORN":   {"pip": 0.001,   "pip_val_per_unit": 0.001},
    "SUGAR":  {"pip": 0.0001,  "pip_val_per_unit": 0.0001},
    "WHEAT":  {"pip": 0.001,   "pip_val_per_unit": 0.001},
    "SOYBN":  {"pip": 0.001,   "pip_val_per_unit": 0.001},
    "SPX":    {"pip": 1.0,     "pip_val_per_unit": 1.0},
    "NAS":    {"pip": 1.0,     "pip_val_per_unit": 1.0},
    "UK1":    {"pip": 1.0,     "pip_val_per_unit": 1.0},
    "DE3":    {"pip": 1.0,     "pip_val_per_unit": 1.0},
    "DEFAULT":{"pip": 0.0001,  "pip_val_per_unit": 0.0001},
}

def get_pip_info(instrument):
    for key, val in PIP_VALUES.items():
        if key != "DEFAULT" and key in instrument:
            return val
    return PIP_VALUES["DEFAULT"]

class PositionCalculator:
    def __init__(self, oanda_client):
        self.client = oanda_client

    def calculate(self, instrument, direction, risk_percent=1.0):
        """
        Calculate position size based on:
        - Account balance
        - Risk percentage (max 5%)
        - ATR-based stop loss
        """
        # Cap risk at 5%
        risk_percent = min(risk_percent, 5.0)
        risk_percent = max(risk_percent, 0.1)

        # Get live price
        price_data = self.client.get_live_price(instrument)
        if not price_data:
            return {"error": "Could not fetch live price"}

        entry = price_data["ask"] if direction=="BUY" else price_data["bid"]
        bid   = price_data["bid"]
        ask   = price_data["ask"]

        # Get account balance
        summary = self.client.get_account_summary()
        balance = float(summary.get("balance", 0))
        nav     = float(summary.get("NAV", balance))

        if balance <= 0:
            return {"error": "Could not fetch account balance"}

        # Get H4 candles for ATR
        try:
            candles = self.client.get_candles(instrument, granularity="H4", count=30)
            if len(candles) >= 15:
                trs = [max(c["high"]-c["low"],
                           abs(c["high"]-candles[i-1]["close"]),
                           abs(c["low"]-candles[i-1]["close"]))
                       for i,c in enumerate(candles[1:],1)]
                atr = sum(trs[-14:])/14
            else:
                atr = entry * 0.002  # fallback 0.2% of price
        except Exception:
            atr = entry * 0.002

        pip_info  = get_pip_info(instrument)
        pip       = pip_info["pip"]

        # SL = 1.5x ATR, TP = 2.5x ATR
        sl_distance = atr * 1.5
        tp_distance = atr * 2.5
        sl_pips     = round(sl_distance / pip, 1)
        tp_pips     = round(tp_distance / pip, 1)

        if direction == "BUY":
            sl_price = round(entry - sl_distance, 5)
            tp_price = round(entry + tp_distance, 5)
        else:
            sl_price = round(entry + sl_distance, 5)
            tp_price = round(entry - tp_distance, 5)

        # Risk amount in account currency
        risk_amount = nav * (risk_percent / 100)

        # Position sizing
        # Units = Risk Amount / (SL distance in price * pip value per unit)
        # For forex: pip value ≈ $10 per standard lot (100,000 units) for USD pairs
        # Simplified: units = risk_amount / sl_distance
        # This gives approximate units based on price risk

        if "JPY" in instrument:
            pip_val_std = 1000  # approx per 100k units
        elif "XAU" in instrument:
            pip_val_std = 1.0
        elif any(x in instrument for x in ["SPX","NAS","UK1","DE3"]):
            pip_val_std = 1.0
        else:
            pip_val_std = 10.0  # per standard lot

        # Units = (Risk / SL_pips) / pip_value_per_unit * 10000
        raw_units = (risk_amount / sl_pips) / pip_val_std * 10000
        units     = max(1, int(raw_units))

        # Min/max limits
        min_units = {"XAU_USD":1,"XAG_USD":1,"XPD_USD":1}.get(instrument, 1)
        units     = max(units, min_units)

        rr_ratio  = round(tp_distance / sl_distance, 2)

        # Margin estimate (rough)
        margin_rate = 0.05  # 5% margin requirement estimate
        margin_required = units * entry * margin_rate

        return {
            "instrument":       instrument,
            "direction":        direction,
            "balance":          round(balance, 2),
            "nav":              round(nav, 2),
            "risk_percent":     risk_percent,
            "risk_amount":      round(risk_amount, 2),
            "entry":            round(entry, 5),
            "bid":              round(bid, 5),
            "ask":              round(ask, 5),
            "stop_loss":        sl_price,
            "take_profit":      tp_price,
            "sl_pips":          sl_pips,
            "tp_pips":          tp_pips,
            "sl_distance":      round(sl_distance, 5),
            "tp_distance":      round(tp_distance, 5),
            "units":            units,
            "rr_ratio":         rr_ratio,
            "atr":              round(atr, 5),
            "margin_required":  round(margin_required, 2),
        }
