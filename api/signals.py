"""
H4 Signal Engine — 8 instruments only
GBP/JPY, EUR/USD, XAU/USD, SUGAR, WHEAT, SPX500, WTI, NATGAS
"""
import logging
from datetime import datetime, date
logger = logging.getLogger(__name__)

try:
    from api.news_check import check_news_blackout
    NEWS_CHECK_AVAILABLE = True
except ImportError:
    try:
        from news_check import check_news_blackout
        NEWS_CHECK_AVAILABLE = True
    except ImportError:
        NEWS_CHECK_AVAILABLE = False

DAILY_LOSS_LIMIT_PCT = 0.05

# ── Your 8 instruments ────────────────────────────────────────────────────────
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

def _f(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None

def _r(v, d=5):
    try:
        return round(float(v), d) if v is not None else None
    except Exception:
        return None

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = float(closes[i]) - float(closes[i - 1])
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    return round(100 - (100 / (1 + ag / al)), 2) if al != 0 else 100.0

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(float(c) for c in closes[:period]) / period
    for c in closes[period:]:
        ema = float(c) * k + ema * (1 - k)
    return round(ema, 6)

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = [
        max(
            float(c["high"]) - float(c["low"]),
            abs(float(c["high"]) - float(candles[i - 1]["close"])),
            abs(float(c["low"])  - float(candles[i - 1]["close"])),
        )
        for i, c in enumerate(candles[1:], 1)
    ]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 6)

def calc_macd(closes, fast=12, slow=26):
    if len(closes) < slow + 1:
        return None, None, None
    ef  = calc_ema(closes, fast)
    es  = calc_ema(closes, slow)
    ef2 = calc_ema(closes[:-1], fast)
    es2 = calc_ema(closes[:-1], slow)
    if not all([ef, es, ef2, es2]):
        return None, None, None
    macd = ef - es
    prev = ef2 - es2
    return round(macd, 6), bool(macd > 0 and prev <= 0), bool(macd < 0 and prev >= 0)

def get_pip(instrument):
    if "JPY"   in instrument: return 0.01
    if "XAU"   in instrument: return 0.1
    if any(x in instrument for x in
           ["WTICO", "XAG", "NATGAS", "SUGAR", "WHEAT", "CORN", "SOYBN"]):
        return 0.01
    if any(x in instrument for x in ["SPX", "NAS", "UK1", "DE3"]):
        return 1.0
    return 0.0001

def get_session():
    h = datetime.utcnow().hour
    if 7  <= h <= 16: return "LONDON",   1.0
    if 13 <= h <= 21: return "NEW_YORK", 1.0
    return "ASIAN", 0.6

def circuit_breaker_active(oanda_client):
    try:
        summary    = oanda_client.get_account_summary()
        balance    = float(summary.get("balance", 0))
        pnl        = oanda_client.get_daily_pnl()
        today_loss = abs(min(0, float(pnl.get("total_pnl", 0))))
        limit      = balance * DAILY_LOSS_LIMIT_PCT
        breached   = today_loss >= limit and limit > 0
        return {
            "active":  breached,
            "loss":    round(today_loss, 2),
            "limit":   round(limit, 2),
            "balance": round(balance, 2),
            "pct":     round(today_loss / balance * 100, 2) if balance > 0 else 0,
        }
    except Exception as e:
        logger.error(f"Circuit breaker error: {e}")
        return {"active": False, "loss": 0, "limit": 0, "balance": 0, "pct": 0}


class SignalEngine:
    def __init__(self, oanda_client):
        self.client   = oanda_client
        self._cb_cache = None
        self._cb_date  = None

    def _check_cb(self):
        today = str(date.today())
        if self._cb_date != today:
            self._cb_cache = circuit_breaker_active(self.client)
            self._cb_date  = today
        return self._cb_cache

    def _safe_price(self, instrument):
        try:
            return _f(self.client.get_live_price(instrument).get("mid", 0))
        except Exception:
            return 0.0

    def analyse(self, instrument):
        try:
            # ── CIRCUIT BREAKER ──────────────────────────────────────────────
            cb = self._check_cb()
            if cb.get("active"):
                return {
                    "instrument": instrument, "timeframe": "H4",
                    "signal": "WAIT", "confidence": 0,
                    "price": self._safe_price(instrument),
                    "entry": None, "sl": None, "tp": None,
                    "sl_pips": None, "tp_pips": None,
                    "rsi": None, "ema20": None, "ema50": None, "ema200": None,
                    "atr": None, "macd": None, "trend": "BLOCKED",
                    "session": get_session()[0], "vol_surge": False,
                    "donchian_high": None, "donchian_low": None,
                    "circuit_breaker": True, "news_blackout": False,
                    "upcoming_events": [],
                    "cb_loss": cb["loss"], "cb_limit": cb["limit"],
                    "reasons": [
                        "DAILY LOSS LIMIT REACHED",
                        f"Lost ${cb['loss']} today ({cb['pct']}% of balance)",
                        f"Limit: 5% = ${cb['limit']}",
                        "Trading suspended until midnight UTC",
                    ],
                    "candle_count": 0,
                }

            # ── NEWS BLACKOUT ────────────────────────────────────────────────
            upcoming = []
            if NEWS_CHECK_AVAILABLE:
                try:
                    news = check_news_blackout(instrument)
                    # FIX: correct key is "in_blackout" not "blocked"
                    if news.get("in_blackout"):
                        blackout = news["active_blackouts"][0] if news.get("active_blackouts") else {}
                        return {
                            "instrument": instrument, "timeframe": "H4",
                            "signal": "WAIT", "confidence": 0,
                            "price": self._safe_price(instrument),
                            "entry": None, "sl": None, "tp": None,
                            "sl_pips": None, "tp_pips": None,
                            "rsi": None, "ema20": None, "ema50": None, "ema200": None,
                            "atr": None, "macd": None, "trend": "NEWS",
                            "session": get_session()[0], "vol_surge": False,
                            "donchian_high": None, "donchian_low": None,
                            "circuit_breaker": False, "news_blackout": True,
                            "news_event": blackout.get("title", ""),
                            "news_time":  blackout.get("time_utc", ""),
                            "upcoming_events": news.get("upcoming_warnings", []),
                            "reasons": [
                                f"NEWS BLACKOUT: {blackout.get('title', '')}",
                                f"Event at {blackout.get('time_utc', '—')} UTC",
                                "Trading blocked 30 min before/after release",
                                "Wait for volatility to settle",
                            ],
                            "candle_count": 0,
                        }
                    upcoming = news.get("upcoming_warnings", [])
                except Exception as e:
                    logger.debug(f"News check error: {e}")

            # ── TECHNICAL ANALYSIS ───────────────────────────────────────────
            candles = self.client.get_candles(instrument, granularity="H4", count=250)
            if len(candles) < 210:
                return {
                    "instrument": instrument, "signal": "WAIT",
                    "reasons": ["Insufficient H4 data"], "confidence": 0,
                    "price": self._safe_price(instrument),
                    "circuit_breaker": False, "news_blackout": False,
                    "upcoming_events": upcoming,
                }

            closes = [float(c["close"])  for c in candles]
            highs  = [float(c["high"])   for c in candles]
            lows   = [float(c["low"])    for c in candles]
            vols   = [int(c["volume"])   for c in candles]

            rsi    = calc_rsi(closes, 14)
            ema20  = calc_ema(closes, 20)
            ema50  = calc_ema(closes, 50)
            ema200 = calc_ema(closes, 200)
            atr    = calc_atr(candles, 14)
            macd, macd_bull, macd_bear = calc_macd(closes)

            don_high = _r(max(highs[-21:-1])) if len(highs) > 21 else None
            don_low  = _r(min(lows[-21:-1]))  if len(lows)  > 21 else None
            latest   = candles[-1]
            prev     = candles[-2]
            price    = float(latest["close"])

            avg_vol   = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1
            vol_surge = bool(vols[-1] > avg_vol * 1.15)
            session, smult = get_session()
            pip   = get_pip(instrument)
            trend = (
                "BULLISH" if ema200 and price > ema200 else
                "BEARISH" if ema200 and price < ema200 else
                "NEUTRAL"
            )

            bs, ss = 0, 0
            br, sr = [], []

            # RSI
            if rsi and rsi < 35:   bs += 30; br.append(f"RSI {rsi:.1f} oversold on H4")
            elif rsi and rsi < 42: bs += 15; br.append(f"RSI {rsi:.1f} approaching oversold")
            if rsi and rsi > 65:   ss += 30; sr.append(f"RSI {rsi:.1f} overbought on H4")
            elif rsi and rsi > 58: ss += 15; sr.append(f"RSI {rsi:.1f} approaching overbought")

            # EMAs
            if ema200 and price > ema200: bs += 20; br.append("Price above 200 EMA — uptrend")
            if ema200 and price < ema200: ss += 20; sr.append("Price below 200 EMA — downtrend")
            if ema50 and ema200 and ema50 > ema200: bs += 12; br.append("Golden cross: 50>200 EMA")
            if ema50 and ema200 and ema50 < ema200: ss += 12; sr.append("Death cross: 50<200 EMA")
            if ema20 and ema50 and ema20 > ema50:   bs += 8;  br.append("20 EMA > 50 EMA")
            if ema20 and ema50 and ema20 < ema50:   ss += 8;  sr.append("20 EMA < 50 EMA")

            # MACD
            if macd_bull: bs += 20; br.append("MACD crossed above zero line")
            if macd_bear: ss += 20; sr.append("MACD crossed below zero line")

            # Donchian breakout
            if don_high and float(prev["close"]) < float(don_high) <= float(latest["close"]):
                bs += 18; br.append(f"H4 breakout above {don_high:.4f}")
            if don_low and float(prev["close"]) > float(don_low) >= float(latest["close"]):
                ss += 18; sr.append(f"H4 breakdown below {don_low:.4f}")

            # Volume
            if vol_surge:
                if bs >= ss: bs += 5; br.append("Volume surge confirms momentum")
                else:        ss += 5; sr.append("Volume surge confirms momentum")

            # Session multiplier
            bs = int(bs * smult)
            ss = int(ss * smult)

            # H1 confirmation
            try:
                h1     = self.client.get_candles(instrument, granularity="H1", count=20)
                h1rsi  = calc_rsi([float(c["close"]) for c in h1], 14)
                if h1rsi and bs > ss and h1rsi < 50:
                    bs += 10; br.append(f"H1 RSI {h1rsi:.0f} confirms bullish bias")
                if h1rsi and ss > bs and h1rsi > 50:
                    ss += 10; sr.append(f"H1 RSI {h1rsi:.0f} confirms bearish bias")
            except Exception:
                pass

            signal = "WAIT"
            confidence = 0
            entry = sl = tp = None
            reasons = [
                f"RSI {rsi:.1f} neutral — no confluence" if rsi else "RSI neutral",
                f"Trend: {trend}",
                f"Session: {session}",
                "Wait for signal confluence",
            ]

            if bs >= 45 and bs > ss:
                signal     = "BUY"
                confidence = min(95, int(bs))
                reasons    = br
                try:
                    entry = float(self.client.get_live_price(instrument)["ask"])
                except Exception:
                    entry = price
                if atr:
                    sl = _r(entry - atr * 1.5, 5)
                    tp = _r(entry + atr * 2.5, 5)

            elif ss >= 45 and ss > bs:
                signal     = "SELL"
                confidence = min(95, int(ss))
                reasons    = sr
                try:
                    entry = float(self.client.get_live_price(instrument)["bid"])
                except Exception:
                    entry = price
                if atr:
                    sl = _r(entry + atr * 1.5, 5)
                    tp = _r(entry - atr * 2.5, 5)

            return {
                "instrument":     instrument,
                "timeframe":      "H4",
                "signal":         signal,
                "confidence":     int(confidence),
                "price":          _r(price, 6),
                "entry":          _r(entry, 6),
                "sl":             _r(sl, 6),
                "tp":             _r(tp, 6),
                "sl_pips":        _r(atr * 1.5 / pip, 1) if atr else None,
                "tp_pips":        _r(atr * 2.5 / pip, 1) if atr else None,
                "rsi":            _r(rsi, 2),
                "ema20":          _r(ema20, 6),
                "ema50":          _r(ema50, 6),
                "ema200":         _r(ema200, 6),
                "atr":            _r(atr, 6),
                "macd":           _r(macd, 6),
                "trend":          trend,
                "session":        session,
                "vol_surge":      vol_surge,
                "donchian_high":  don_high,
                "donchian_low":   don_low,
                "reasons":        reasons[:4],
                "candle_count":   int(len(candles)),
                "circuit_breaker":False,
                "news_blackout":  False,
                "upcoming_events":upcoming,
            }

        except Exception as e:
            logger.error(f"Signal error {instrument}: {e}")
            return {
                "instrument":     instrument,
                "signal":         "ERROR",
                "error":          str(e),
                "reasons":        [str(e)],
                "confidence":     0,
                "price":          0,
                "circuit_breaker":False,
                "news_blackout":  False,
                "upcoming_events":[],
            }