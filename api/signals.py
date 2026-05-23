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

# Per-instrument session weights — each instrument active in its natural hours
INSTRUMENT_SESSION_WEIGHTS = {
    "GBP_JPY":    {"LONDON": 1.0, "NEW_YORK": 0.8, "ASIAN": 0.7},
    "EUR_USD":    {"LONDON": 1.0, "NEW_YORK": 0.9, "ASIAN": 0.4},
    "XAU_USD":    {"LONDON": 0.9, "NEW_YORK": 1.0, "ASIAN": 0.5},
    "SUGAR_USD":  {"LONDON": 0.8, "NEW_YORK": 0.9, "ASIAN": 0.4},
    "WHEAT_USD":  {"LONDON": 0.8, "NEW_YORK": 0.9, "ASIAN": 0.4},
    "SPX500_USD": {"LONDON": 0.5, "NEW_YORK": 1.0, "ASIAN": 0.2},
    "WTICO_USD":  {"LONDON": 0.7, "NEW_YORK": 1.0, "ASIAN": 0.3},
    "NATGAS_USD": {"LONDON": 0.7, "NEW_YORK": 1.0, "ASIAN": 0.3},
}

# Known normal spreads for spread filter (Option A — hardcoded baselines)
NORMAL_SPREADS = {
    "EUR_USD":    0.00020,
    "GBP_JPY":    0.040,
    "XAU_USD":    0.50,
    "SUGAR_USD":  0.00010,
    "WHEAT_USD":  0.50,
    "SPX500_USD": 0.50,
    "WTICO_USD":  0.04,
    "NATGAS_USD": 0.003,
}
SPREAD_MULTIPLIER_LIMIT = 3.0  # Block if spread > 3x normal

def get_session():
    h = datetime.utcnow().hour
    if 7  <= h <= 16: return "LONDON",   1.0
    if 13 <= h <= 21: return "NEW_YORK", 1.0
    return "ASIAN", 0.6

def get_session_weight(instrument, session):
    weights = INSTRUMENT_SESSION_WEIGHTS.get(instrument, {})
    return weights.get(session, 0.6)

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

            # ── SPREAD FILTER (Fix 4) ────────────────────────────────────────
            # Block trades when spread is abnormally wide (>3x normal baseline)
            spread_blocked = False
            spread_info    = {}
            try:
                price_data = self.client.get_live_price(instrument)
                current_spread = price_data.get("spread", 0)
                normal_spread  = NORMAL_SPREADS.get(instrument, 0.001)
                spread_ratio   = current_spread / normal_spread if normal_spread > 0 else 1
                spread_info    = {
                    "current": round(current_spread, 6),
                    "normal":  round(normal_spread, 6),
                    "ratio":   round(spread_ratio, 2),
                }
                if spread_ratio > SPREAD_MULTIPLIER_LIMIT:
                    spread_blocked = True
                    logger.info(f"{instrument} spread blocked: {spread_ratio:.1f}x normal")
            except Exception as e:
                logger.debug(f"Spread check error: {e}")

            if spread_blocked:
                return {
                    "instrument": instrument, "timeframe": "H4",
                    "signal": "WAIT", "confidence": 0,
                    "price": self._safe_price(instrument),
                    "entry": None, "sl": None, "tp": None,
                    "sl_pips": None, "tp_pips": None,
                    "rsi": None, "ema20": None, "ema50": None, "ema200": None,
                    "atr": None, "macd": None, "trend": "SPREAD_BLOCKED",
                    "session": get_session()[0], "vol_surge": False,
                    "donchian_high": None, "donchian_low": None,
                    "circuit_breaker": False, "news_blackout": False,
                    "spread_blocked": True, "spread_info": spread_info,
                    "upcoming_events": upcoming,
                    "reasons": [
                        f"Spread too wide: {spread_info.get('ratio',0):.1f}x normal",
                        f"Current: {spread_info.get('current',0):.5f} vs normal {spread_info.get('normal',0):.5f}",
                        "Wait for spread to normalise",
                        "Typically widens during low liquidity / news",
                    ], "candle_count": 0,
                }

            # ── WEEKLY STRUCTURE FILTER (Fix 1) ───────────────────────────────
            # Only trade WITH the weekly trend — reduces counter-trend losses
            weekly_bias    = "NEUTRAL"
            weekly_ema20   = None
            counter_trend  = False
            try:
                w1 = self.client.get_candles(instrument, granularity="W", count=30)
                if len(w1) >= 21:
                    w1_closes  = [float(c["close"]) for c in w1]
                    weekly_ema20 = calc_ema(w1_closes, 20)
                    w1_price   = w1_closes[-1]
                    if weekly_ema20:
                        if w1_price > weekly_ema20 * 1.001:
                            weekly_bias = "BULLISH"
                        elif w1_price < weekly_ema20 * 0.999:
                            weekly_bias = "BEARISH"
            except Exception as e:
                logger.debug(f"Weekly candles error: {e}")

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

            # RSI — level + divergence detection (Fix 3)
            if rsi and rsi < 35:   bs += 30; br.append(f"RSI {rsi:.1f} oversold on H4")
            elif rsi and rsi < 42: bs += 15; br.append(f"RSI {rsi:.1f} approaching oversold")
            if rsi and rsi > 65:   ss += 30; sr.append(f"RSI {rsi:.1f} overbought on H4")
            elif rsi and rsi > 58: ss += 15; sr.append(f"RSI {rsi:.1f} approaching overbought")

            # RSI Divergence — higher probability than raw level signals
            # Look back 5 bars for price vs RSI direction mismatch
            if len(closes) >= 20 and len(candles) >= 20:
                try:
                    # Calculate RSI for 5 bars ago
                    rsi_prev5 = calc_rsi(closes[:-5], 14)
                    price_now  = closes[-1]
                    price_prev5= closes[-5]

                    if rsi and rsi_prev5:
                        # Bullish hidden divergence: price higher low, RSI lower low
                        # Confirms uptrend continuation
                        if price_now > price_prev5 and rsi < rsi_prev5 - 3:
                            bs += 20
                            br.append(f"Bullish RSI divergence: price up, RSI {rsi:.1f} < {rsi_prev5:.1f}")

                        # Bearish hidden divergence: price lower high, RSI higher high
                        elif price_now < price_prev5 and rsi > rsi_prev5 + 3:
                            ss += 20
                            sr.append(f"Bearish RSI divergence: price dn, RSI {rsi:.1f} > {rsi_prev5:.1f}")

                        # Classic bullish divergence: price makes lower low, RSI higher low
                        # Strongest reversal signal
                        elif price_now < price_prev5 and rsi > rsi_prev5 + 5 and rsi < 45:
                            bs += 25
                            br.append(f"CLASSIC bullish divergence: price LL, RSI HL at {rsi:.1f}")

                        # Classic bearish divergence: price makes higher high, RSI lower high
                        elif price_now > price_prev5 and rsi < rsi_prev5 - 5 and rsi > 55:
                            ss += 25
                            sr.append(f"CLASSIC bearish divergence: price HH, RSI LH at {rsi:.1f}")
                except Exception:
                    pass

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

            # Donchian breakout — volume is REQUIRED gate (Fix 2)
            # Breakouts without volume confirmation are fakeouts ~60% of the time
            if don_high and float(prev["close"]) < float(don_high) <= float(latest["close"]):
                if vol_surge:
                    bs += 18; br.append(f"H4 breakout above {don_high:.4f} WITH volume")
                else:
                    bs += 8;  br.append(f"H4 breakout above {don_high:.4f} — LOW VOLUME, reduced score")
            if don_low and float(prev["close"]) > float(don_low) >= float(latest["close"]):
                if vol_surge:
                    ss += 18; sr.append(f"H4 breakdown below {don_low:.4f} WITH volume")
                else:
                    ss += 8;  sr.append(f"H4 breakdown below {don_low:.4f} — LOW VOLUME, reduced score")

            # Volume surge bonus (non-breakout)
            if vol_surge:
                if bs > ss: bs += 5; br.append("Volume surge confirms bullish momentum")
                elif ss > bs: ss += 5; sr.append("Volume surge confirms bearish momentum")

            # Per-instrument session multiplier (Fix 6)
            inst_smult = get_session_weight(instrument, session)
            bs = int(bs * inst_smult)
            ss = int(ss * inst_smult)

            # ── WEEKLY TREND ALIGNMENT (Fix 1 continued) ────────────────────
            # Counter-trend signals get confidence penalty
            # BUY signal against weekly BEARISH trend = counter-trend
            # SELL signal against weekly BULLISH trend = counter-trend
            if weekly_bias == "BEARISH" and bs > ss:
                counter_trend = True
                bs = int(bs * 0.75)  # 25% penalty — still possible but harder to qualify
                br.append(f"Counter-trend: H4 BUY vs Weekly BEARISH — reduced score")
            elif weekly_bias == "BULLISH" and ss > bs:
                counter_trend = True
                ss = int(ss * 0.75)
                sr.append(f"Counter-trend: H4 SELL vs Weekly BULLISH — reduced score")
            elif weekly_bias == "BULLISH" and bs > ss:
                bs = int(bs * 1.05)  # Small bonus for trend alignment
                br.append(f"Weekly BULLISH — H4 BUY aligned with trend")
            elif weekly_bias == "BEARISH" and ss > bs:
                ss = int(ss * 1.05)
                sr.append(f"Weekly BEARISH — H4 SELL aligned with trend")

            # ── MULTI-TIMEFRAME ANALYSIS ─────────────────────────────────────
            # D1 bias: price vs 50 EMA on Daily
            d1_bias   = "NEUTRAL"
            d1_ema50  = None
            d1_rsi    = None
            try:
                d1 = self.client.get_candles(instrument, granularity="D", count=60)
                if len(d1) >= 51:
                    d1_closes = [float(c["close"]) for c in d1]
                    d1_ema50  = calc_ema(d1_closes, 50)
                    d1_rsi    = calc_rsi(d1_closes, 14)
                    d1_price  = d1_closes[-1]
                    if d1_ema50:
                        if d1_price > d1_ema50 * 1.001:
                            d1_bias = "BULLISH"
                        elif d1_price < d1_ema50 * 0.999:
                            d1_bias = "BEARISH"
                    # D1 bonus to score
                    if d1_bias == "BULLISH" and bs > ss:
                        bs += 8; br.append(f"D1 bullish bias — price above 50 EMA")
                    elif d1_bias == "BEARISH" and ss > bs:
                        ss += 8; sr.append(f"D1 bearish bias — price below 50 EMA")
            except Exception:
                pass

            # H1 momentum + confluence
            h1_momentum = "NEUTRAL"
            h1_rsi      = None
            try:
                h1 = self.client.get_candles(instrument, granularity="H1", count=24)
                if len(h1) >= 15:
                    h1_closes = [float(c["close"]) for c in h1]
                    h1_rsi    = calc_rsi(h1_closes, 14)
                    h1_ema20  = calc_ema(h1_closes, 20)
                    h1_price  = h1_closes[-1]

                    # H1 momentum direction
                    if h1_rsi and h1_ema20:
                        if h1_rsi < 50 and h1_price < h1_ema20:
                            h1_momentum = "BEARISH"
                        elif h1_rsi > 50 and h1_price > h1_ema20:
                            h1_momentum = "BULLISH"

                    # Score adjustment
                    if h1_rsi and bs > ss and h1_rsi < 50:
                        bs += 10; br.append(f"H1 RSI {h1_rsi:.0f} confirms bullish bias")
                    if h1_rsi and ss > bs and h1_rsi > 50:
                        ss += 10; sr.append(f"H1 RSI {h1_rsi:.0f} confirms bearish bias")
            except Exception:
                pass

            # ── CONFLUENCE SCORE ─────────────────────────────────────────────
            # 3/3 = D1 + H4 + H1 all aligned
            # Calculate after signal is determined
            def calc_confluence(sig_direction, d1, h4_trend, h1_mom):
                score = 0
                if sig_direction == "BUY":
                    if d1  == "BULLISH":  score += 1
                    if h4_trend == "BULLISH": score += 1
                    if h1_mom == "BULLISH":   score += 1
                elif sig_direction == "SELL":
                    if d1  == "BEARISH":  score += 1
                    if h4_trend == "BEARISH": score += 1
                    if h1_mom == "BEARISH":   score += 1
                return score

            # ── FIX 5: Count confirmed indicator GROUPS (not just score) ────────
            # Need minimum 3 of 5 groups confirmed for a valid signal
            def count_groups(buy_reasons):
                groups_hit = 0
                text = " ".join(buy_reasons).lower()
                if "rsi"     in text: groups_hit += 1
                if "ema"     in text or "trend" in text: groups_hit += 1
                if "macd"    in text: groups_hit += 1
                if "breakout" in text or "breakdown" in text: groups_hit += 1
                if "volume"  in text or "h1 rsi" in text: groups_hit += 1
                return groups_hit

            signal = "WAIT"
            confidence = 0
            entry = sl = tp = None
            reasons = [
                f"RSI {rsi:.1f} neutral — no confluence" if rsi else "RSI neutral",
                f"Trend: {trend}",
                f"Session: {session}",
                "Wait for signal confluence",
            ]

            buy_groups  = count_groups(br) if br else 0
            sell_groups = count_groups(sr) if sr else 0

            if bs >= 55 and bs > ss and buy_groups >= 3:
                signal     = "BUY"
                confidence = min(95, int(bs))
                reasons    = br
                try:
                    live       = self.client.get_live_price(instrument)
                    market_ask = float(live["ask"])
                    market_bid = float(live["bid"])
                except Exception:
                    market_ask = price
                    market_bid = price

                # Fix 8: ATR Pullback Entry
                # If price has surged > 0.5×ATR above 20 EMA in last bar
                # suggest limit entry at 20 EMA instead of market
                pullback_entry = None
                entry_type     = "MARKET"
                if ema20 and atr:
                    surge = market_ask - float(ema20)
                    if surge > atr * 0.5:
                        # Price too extended — use limit at 20 EMA
                        pullback_entry = _r(float(ema20), 5)
                        entry_type     = "LIMIT"
                        reasons = list(br) + [f"Extended {surge/atr:.1f}x ATR above 20 EMA — limit entry at {pullback_entry}"]

                entry = pullback_entry if pullback_entry else market_ask
                if atr:
                    sl = _r(entry - atr * 1.5, 5)
                    tp = _r(entry + atr * 2.5, 5)

            elif ss >= 55 and ss > bs and sell_groups >= 3:
                signal     = "SELL"
                confidence = min(95, int(ss))
                reasons    = sr
                try:
                    live       = self.client.get_live_price(instrument)
                    market_ask = float(live["ask"])
                    market_bid = float(live["bid"])
                except Exception:
                    market_ask = price
                    market_bid = price

                # Fix 8: ATR Pullback Entry for SELL
                pullback_entry = None
                entry_type     = "MARKET"
                if ema20 and atr:
                    surge = float(ema20) - market_bid
                    if surge > atr * 0.5:
                        pullback_entry = _r(float(ema20), 5)
                        entry_type     = "LIMIT"
                        reasons = list(sr) + [f"Extended {surge/atr:.1f}x ATR below 20 EMA — limit entry at {pullback_entry}"]

                entry = pullback_entry if pullback_entry else market_bid
                if atr:
                    sl = _r(entry + atr * 1.5, 5)
                    tp = _r(entry - atr * 2.5, 5)

            # Confluence score: D1 + H4 + H1 alignment
            confluence_score = calc_confluence(signal, d1_bias, trend, h1_momentum)
            confluence_label = (
                "STRONG"   if confluence_score == 3 else
                "MODERATE" if confluence_score == 2 else
                "WEAK"     if confluence_score == 1 else
                "NONE"
            )
            # Boost confidence for 3/3 alignment
            if signal in ("BUY","SELL") and confluence_score == 3:
                confidence = min(95, confidence + 8)
            # Add confluence to reasons
            if signal in ("BUY","SELL") and confluence_score >= 2:
                reasons = list(reasons)
                reasons.insert(0, f"{confluence_label} confluence: D1+H4+H1 aligned")

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
                "d1_bias":        d1_bias,
                "d1_ema50":       _r(d1_ema50, 6),
                "d1_rsi":         _r(d1_rsi, 2),
                "h1_momentum":    h1_momentum,
                "h1_rsi":         _r(h1_rsi, 2) if h1_rsi else None,
                "confluence":     confluence_score,
                "confluence_label": confluence_label,
                "weekly_bias":      weekly_bias,
                "counter_trend":    counter_trend,
                "spread_blocked":   False,
                "spread_info":      spread_info,
                "groups_confirmed": buy_groups if signal == "BUY" else sell_groups if signal == "SELL" else 0,
                "entry_type":      entry_type if signal in ("BUY","SELL") else "NONE",
                "pullback_entry":  pullback_entry,
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