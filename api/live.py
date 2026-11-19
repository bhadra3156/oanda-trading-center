"""
Live board engine for the OANDA Trading Machine.

Pulls candles and prices from OANDA, computes the indicator readings the
Trading Machine grades (open equivalents of the trader's TradingView set-up),
and builds one board snapshot for all markets.

Read-only: nothing here places, changes or closes an order.
The maths lives in pure functions so it can be tested without OANDA.
"""
import math, time, logging, threading
from collections import deque
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

BOARD_INSTRUMENTS = [
    "XAU_USD", "NATGAS_USD", "WTICO_USD", "CORN_USD", "WHEAT_USD", "SUGAR_USD",
    "SPX500_USD", "UK100_GBP", "GBP_JPY", "GBP_CAD", "USD_JPY", "EUR_USD", "GBP_USD",
]
FX_PAIRS = ["GBP_USD", "EUR_USD", "USD_JPY", "GBP_JPY", "GBP_CAD"]
POSITION_BOOK = ["EUR_USD", "GBP_USD", "USD_JPY", "GBP_JPY", "XAU_USD"]


# ════════════════════════════ indicator maths (pure) ════════════════════════════
def ema(vals, n):
    out, k, e = [], 2.0 / (n + 1), None
    for i, v in enumerate(vals):
        if e is None:
            if i + 1 < n:
                out.append(None); continue
            e = sum(vals[i + 1 - n:i + 1]) / n
        else:
            e = v * k + e * (1 - k)
        out.append(e)
    return out


def wilder(vals, n):
    out, s = [], None
    for i, v in enumerate(vals):
        if s is None:
            if i + 1 < n:
                out.append(None); continue
            s = sum(vals[i + 1 - n:i + 1]) / n
        else:
            s = (s * (n - 1) + v) / n
        out.append(s)
    return out


def rsi(closes, n=14):
    gains, losses = [0.0], [0.0]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    ag, al = wilder(gains[1:], n), wilder(losses[1:], n)
    out = [None]
    for g, l in zip(ag, al):
        if g is None: out.append(None)
        elif l == 0: out.append(100.0)
        else: out.append(100 - 100 / (1 + g / l))
    return out


def true_ranges(h, l, c):
    tr = [h[0] - l[0]]
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return tr


def atr(h, l, c, n=14):
    return wilder(true_ranges(h, l, c), n)


def macd_hist(closes, f=12, s=26, sig=9):
    ef, es = ema(closes, f), ema(closes, s)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es)]
    valid = [x for x in line if x is not None]
    sl = ema(valid, sig)
    pad = len(line) - len(valid)
    sig_full = [None] * pad + sl
    return [a - b if a is not None and b is not None else None for a, b in zip(line, sig_full)]


def psar(h, l, step=0.02, mx=0.2):
    """Returns (sar list, trend list +1/-1, af list)."""
    n = len(h)
    if n < 3:
        return [None] * n, [0] * n, [0] * n
    trend = 1 if h[1] + l[1] >= h[0] + l[0] else -1
    sar = l[0] if trend == 1 else h[0]
    ep = h[1] if trend == 1 else l[1]
    af = step
    out_s, out_t, out_a = [None, sar], [trend, trend], [af, af]
    for i in range(2, n):
        sar = sar + af * (ep - sar)
        if trend == 1:
            sar = min(sar, l[i - 1], l[i - 2])
            if l[i] < sar:
                trend, sar, ep, af = -1, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, mx)
        else:
            sar = max(sar, h[i - 1], h[i - 2])
            if h[i] > sar:
                trend, sar, ep, af = 1, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, mx)
        out_s.append(sar); out_t.append(trend); out_a.append(af)
    return out_s, out_t, out_a


def _mid(h, l, i, n):
    if i + 1 < n:
        return None
    return (max(h[i + 1 - n:i + 1]) + min(l[i + 1 - n:i + 1])) / 2


def ichimoku(h, l, c, t=9, k=26, b=52):
    n = len(c)
    ten = [_mid(h, l, i, t) for i in range(n)]
    kij = [_mid(h, l, i, k) for i in range(n)]
    spanA = [(a + b_) / 2 if a is not None and b_ is not None else None for a, b_ in zip(ten, kij)]
    spanB = [_mid(h, l, i, b) for i in range(n)]
    i = n - 1
    # cloud under the current bar was plotted 26 bars ago
    a_now = spanA[i - k] if i - k >= 0 else None
    b_now = spanB[i - k] if i - k >= 0 else None
    return {
        "tenkan": ten[i], "kijun": kij[i],
        "cloudTop": max(a_now, b_now) if a_now is not None and b_now is not None else None,
        "cloudBottom": min(a_now, b_now) if a_now is not None and b_now is not None else None,
        "futureA": spanA[i], "futureB": spanB[i],
    }


def pivots(h, l, k=3):
    """Swing highs/lows confirmed by k bars each side: lists of (index, price)."""
    hi, lo = [], []
    for i in range(k, len(h) - k):
        if h[i] == max(h[i - k:i + k + 1]) and h[i] > max(h[i - k:i]):
            hi.append((i, h[i]))
        if l[i] == min(l[i - k:i + k + 1]) and l[i] < min(l[i - k:i]):
            lo.append((i, l[i]))
    return hi, lo


def divergence(h, l, r, lookback=60, k=3):
    """Latest regular/hidden RSI divergence between the last two swings. Returns (label, bars_ago)."""
    n = len(h)
    hi, lo = pivots(h, l, k)
    hi = [p for p in hi if p[0] >= n - lookback and r[p[0]] is not None]
    lo = [p for p in lo if p[0] >= n - lookback and r[p[0]] is not None]
    found = []
    if len(lo) >= 2:
        (i1, p1), (i2, p2) = lo[-2], lo[-1]
        if p2 < p1 and r[i2] > r[i1]: found.append(("Regular Bull", n - 1 - i2))
        elif p2 > p1 and r[i2] < r[i1]: found.append(("Hidden Bull", n - 1 - i2))
    if len(hi) >= 2:
        (i1, p1), (i2, p2) = hi[-2], hi[-1]
        if p2 > p1 and r[i2] < r[i1]: found.append(("Regular Bear", n - 1 - i2))
        elif p2 < p1 and r[i2] > r[i1]: found.append(("Hidden Bear", n - 1 - i2))
    if not found:
        return "None", None
    found.sort(key=lambda x: x[1])
    return found[0]


def reversal_signal(h, l, c, r, k=3, lookback=40):
    """Bj-Reversal-style: most recent confirmed swing low with RSI < 42 (buy) or swing high with RSI > 58 (sell)."""
    n = len(c)
    hi, lo = pivots(h, l, k)
    cands = [("Buy (blue)", i) for i, _ in lo if r[i] is not None and r[i] < 42] + \
            [("Sell (red)", i) for i, _ in hi if r[i] is not None and r[i] > 58]
    cands = [x for x in cands if x[1] >= n - lookback]
    if not cands:
        return "None", None
    lab, i = max(cands, key=lambda x: x[1])
    return lab, n - 1 - i


def slope_dir(closes, n=50, back=5):
    e = ema(closes, n)
    if len(e) <= back or e[-1] is None or e[-1 - back] is None:
        return "flat"
    up = closes[-1] > e[-1] and e[-1] > e[-1 - back]
    dn = closes[-1] < e[-1] and e[-1] < e[-1 - back]
    return "up" if up else "down" if dn else "flat"


def round_to(x, dp):
    return None if x is None else round(x, dp)


# ════════════════════════════ one market ════════════════════════════
def analyse(h4, d1, h1, price=None, dp=5):
    """h4/d1/h1: lists of completed candles {time, open, high, low, close, volume};
    d1 may end with today's incomplete candle flagged complete=False."""
    if len(h4) < 60:
        return {"ok": False, "error": "not enough 4H history"}
    H, L, C, O, V = ([x[k] for x in h4] for k in ("high", "low", "close", "open", "volume"))
    last = C[-1]
    px = price if price else last
    e50, e200 = ema(C, 50), ema(C, 200)
    R = rsi(C, 14)
    A = atr(H, L, C, 14)
    mh = macd_hist(C)
    sar, st, af = psar(H, L)
    ich = ichimoku(H, L, C)

    trend = "Bullish" if (e50[-1] or 0) >= (e200[-1] or e50[-1] or 0) else "Bearish"
    ema200 = None if e200[-1] is None else ("Above" if px >= e200[-1] else "Below")

    # higher timeframe = daily
    dC = [x["close"] for x in d1 if x.get("complete", True)]
    htf = None
    if len(dC) >= 60:
        d50, d200 = ema(dC, 50), ema(dC, min(200, len(dC) - 1))
        if d200[-1] is not None:
            b = (d50[-1] > d200[-1]) + (dC[-1] > d200[-1])
            htf = "Bullish" if b == 2 else "Bearish" if b == 0 else ("Bullish" if dC[-1] > d50[-1] else "Bearish")

    # PSAR strength: acceleration factor rising in the last 3 bars = strengthening
    ps = None
    if st[-1]:
        stronger = af[-1] > af[-4] if len(af) > 4 else True
        ps = ("Bull" if st[-1] == 1 else "Bear") + (" · strengthening" if stronger else " · weakening")

    mom = None
    if mh[-1] is not None and mh[-2] is not None:
        mom = ("Rising" if mh[-1] > mh[-2] else "Falling") + (" · above 0" if mh[-1] >= 0 else " · below 0")

    upv = sum(v for o, c, v in zip(O[-20:], C[-20:], V[-20:]) if c > o)
    dnv = sum(v for o, c, v in zip(O[-20:], C[-20:], V[-20:]) if c < o)
    ratio = (upv + 1) / (dnv + 1)
    vol = "Buyers in control" if ratio >= 1.15 else "Sellers in control" if ratio <= 1 / 1.15 else "Balanced"

    cloud = None
    if ich["cloudTop"] is not None:
        cloud = "Above cloud" if px > ich["cloudTop"] else "Below cloud" if px < ich["cloudBottom"] else "Inside cloud"
    kumo = None
    if ich["futureA"] is not None and ich["futureB"] is not None:
        kumo = "Green (bullish)" if ich["futureA"] >= ich["futureB"] else "Grey/red (bearish)"

    div, div_bars = divergence(H, L, R)
    bj, bj_bars = reversal_signal(H, L, C, R)

    v20 = sum(V[-20:]) / 20.0
    v100 = sum(V[-100:]) / float(min(100, len(V)))
    v5 = sum(V[-5:]) / 5.0
    if v100 and v20 / v100 >= 1.15:
        mcdx = "Strong & rising" if v5 >= v20 else "Strong & fading"
    else:
        mcdx = "Weak / absent"

    # composite bias 0..100
    parts = [
        (1 if trend == "Bullish" else -1, 1.5),
        ((1 if htf == "Bullish" else -1) if htf else 0, 2),
        ((1 if cloud == "Above cloud" else -1 if cloud == "Below cloud" else 0) if cloud else 0, 2),
        ((1 if ema200 == "Above" else -1) if ema200 else 0, 1.5),
        ((1 if st[-1] == 1 else -1), 1),
        ((1 if (mh[-1] or 0) > 0 else -1), 1),
        ((1 if (R[-1] or 50) >= 55 else -1 if (R[-1] or 50) <= 45 else 0), 1),
    ]
    tot = sum(w for _, w in parts)
    score = round(50 + 50 * sum(s * w for s, w in parts) / tot)
    bias = "Strong Bull" if score >= 78 else "Bull" if score >= 58 else "Strong Bear" if score <= 22 else "Bear" if score <= 42 else "Neutral"

    # levels: previous day high/low and nearest swing levels
    dcomp = [x for x in d1 if x.get("complete", True)]
    pdh = dcomp[-1]["high"] if dcomp else None
    pdl = dcomp[-1]["low"] if dcomp else None
    shi, slo = pivots(H[-150:], L[-150:], 3)
    res = min([p for _, p in shi if p > px], default=None)
    sup = max([p for _, p in slo if p < px], default=None)
    if res is None: res = max(H[-50:]) if max(H[-50:]) > px else None
    if sup is None: sup = min(L[-50:]) if min(L[-50:]) < px else None

    # volatility (daily)
    vola = None
    dH = [x["high"] for x in dcomp]; dL = [x["low"] for x in dcomp]; dCc = [x["close"] for x in dcomp]
    if len(dcomp) >= 25:
        tr = true_ranges(dH, dL, dCc)
        adr = sum(tr[-14:]) / 14.0
        r5, r20 = sum(tr[-5:]) / 5.0, sum(tr[-20:]) / 20.0
        today = next((x for x in reversed(d1) if not x.get("complete", True)), None)
        used = ((today["high"] - today["low"]) / adr) if (today and adr) else None
        ratio_v = r5 / r20 if r20 else None
        vola = {
            "adr": round_to(adr, dp), "ratio": round(ratio_v, 2) if ratio_v else None,
            "regime": None if not ratio_v else ("Quiet" if ratio_v < 0.8 else "Wild" if ratio_v > 1.3 else "Normal"),
            "adrUsedPct": round(used * 100) if used is not None else None,
        }

    h1C = [x["close"] for x in h1]
    mtf = {
        "D": slope_dir(dCc) if len(dCc) >= 60 else "flat",
        "H4": slope_dir(C),
        "H1": slope_dir(h1C) if len(h1C) >= 60 else "flat",
    }

    read = {
        "bias": bias, "biasScore": score, "trend": trend, "htf": htf, "psar": ps, "mom": mom, "vol": vol,
        "div": div, "divBars": div_bars, "cloud": cloud, "kumo": kumo, "ema200": ema200, "bj": bj, "bjBars": bj_bars,
        "mcdx": mcdx, "rsi": round(R[-1], 1) if R[-1] is not None else None,
        "price": round_to(px, dp), "atr": round_to(A[-1], dp), "pdh": round_to(pdh, dp), "pdl": round_to(pdl, dp),
        "res": round_to(res, dp), "sup": round_to(sup, dp),
    }
    read = {k: v for k, v in read.items() if v is not None}
    levels = {
        "ema50": round_to(e50[-1], dp), "ema200": round_to(e200[-1], dp), "kijun": round_to(ich["kijun"], dp),
        "tenkan": round_to(ich["tenkan"], dp), "cloudTop": round_to(ich["cloudTop"], dp), "cloudBottom": round_to(ich["cloudBottom"], dp),
        "psar": round_to(sar[-1], dp),
    }
    chg24 = None
    if len(h1C) >= 25 and h1C[-25]:
        chg24 = (px - h1C[-25]) / h1C[-25] * 100
    return {
        "ok": True, "read": read, "levels": levels, "mtf": mtf, "volatility": vola,
        "change24hPct": round(chg24, 3) if chg24 is not None else None,
        "lastCandle": h4[-1]["time"], "spark": [round_to(x, dp) for x in C[-30:]],
    }


def currency_strength(changes):
    """changes: {pair: pct change}. Average signed move per currency, scaled to -100..100."""
    acc = {}
    for pair, ch in changes.items():
        if ch is None: continue
        b, q = pair.split("_")
        acc.setdefault(b, []).append(ch); acc.setdefault(q, []).append(-ch)
    raw = {k: sum(v) / len(v) for k, v in acc.items()}
    mx = max([abs(v) for v in raw.values()] + [1e-9])
    return sorted([{"ccy": k, "score": round(v / mx * 100), "pct": round(v, 3)} for k, v in raw.items()], key=lambda x: -x["score"])


# ════════════════════════════ grading (mirror of the Machine's engine) ════════════════════════════
_W = [("bias", {"Strong Bull": 2, "Bull": 1, "Neutral": 0, "Bear": -1, "Strong Bear": -2}, 1.5),
      ("trend", {"Bullish": 1, "Bearish": -1}, 1.5), ("htf", {"Bullish": 1, "Bearish": -1}, 2),
      ("psar", {"Bull · strengthening": 1, "Bull · weakening": .5, "Bear · weakening": -.5, "Bear · strengthening": -1}, 1),
      ("mom", {"Rising · above 0": 1, "Rising · below 0": .5, "Falling · above 0": -.5, "Falling · below 0": -1}, 1),
      ("vol", {"Buyers in control": 1, "Balanced": 0, "Sellers in control": -1}, 1),
      ("div", {"None": 0, "RSI Regression Bull": 1, "RSI Regression Bear": -1, "Regular Bull": 1, "Regular Bear": -1, "Hidden Bull": 1, "Hidden Bear": -1}, 1),
      ("cloud", {"Above cloud": 1, "Inside cloud": 0, "Below cloud": -1}, 2),
      ("kumo", {"Green (bullish)": 1, "Grey/red (bearish)": -1}, .5), ("ema200", {"Above": 1, "Below": -1}, 1.5),
      ("bj", {"None": 0, "Buy (blue)": 1, "Sell (red)": -1}, 1)]


def grade(read):
    """Simplified server-side copy of the Machine's confluence grade, used for Telegram alerts."""
    S, total = 0.0, 0.0
    for k, m, w in _W:
        total += (2 if k == "bias" else 1) * w
        v = m.get(read.get(k))
        if v is None: continue
        mult = 1.0
        if k == "div" and v and (read.get("divBars") or 0) > 15: mult = 0.5
        if k == "bj" and v and (read.get("bjBars") or 0) > 20: mult = 0.5
        S += v * w * mult
    r = read.get("rsi")
    total += 0.5
    if r is not None: S += 0.5 if r >= 55 else -0.5 if r <= 45 else 0
    net = round(S / total * 100)
    sg = 1 if net >= 15 else -1 if net <= -15 else 0
    if not sg or read.get("cloud") == "Inside cloud":
        return {"net": net, "dir": "none" if not sg else ("long" if sg > 0 else "short"), "grade": "SKIP"}
    htf = {"Bullish": 1, "Bearish": -1}.get(read.get("htf"))
    tr = {"Bullish": 1, "Bearish": -1}.get(read.get("trend"))
    if htf == -sg and tr == -sg:
        g = "SKIP"
    elif abs(net) >= 55 and htf == sg and {"Above cloud": 1, "Below cloud": -1}.get(read.get("cloud")) == sg \
            and {"Above": 1, "Below": -1}.get(read.get("ema200")) != -sg and {"Buyers in control": 1, "Sellers in control": -1}.get(read.get("vol")) != -sg:
        g = "A"
    elif abs(net) >= 35 and htf != -sg:
        g = "B"
    else:
        g = "C"
    return {"net": net, "dir": "long" if sg > 0 else "short", "grade": g}


# ════════════════════════════ H4 candle clock ════════════════════════════
def h4_boundaries(now=None):
    """OANDA 4H candles align to 17:00 New York. Returns (last_close_utc, next_close_utc)."""
    try:
        from zoneinfo import ZoneInfo
        ny = ZoneInfo("America/New_York")
    except Exception:
        ny = timezone(timedelta(hours=-5))
    now = now or datetime.now(timezone.utc)
    loc = now.astimezone(ny)
    anchor = loc.replace(hour=17, minute=0, second=0, microsecond=0)
    if anchor > loc: anchor -= timedelta(days=1)
    steps = int((loc - anchor).total_seconds() // (4 * 3600))
    last = anchor + timedelta(hours=4 * steps)
    return last.astimezone(timezone.utc), (last + timedelta(hours=4)).astimezone(timezone.utc)


# ════════════════════════════ live engine (talks to OANDA) ════════════════════════════
class LiveBoard:
    def __init__(self, oanda, dps=None):
        self.oanda = oanda
        self.dps = dps or {}
        self._board = None
        self._board_lock = threading.Lock()
        self._prices = None
        self._spreads = {i: deque(maxlen=300) for i in BOARD_INSTRUMENTS}
        self._books = {}
        self._pool = ThreadPoolExecutor(max_workers=8)

    # -- raw OANDA reads --
    def candles(self, inst, gran, count, include_live=False):
        import oandapyV20.endpoints.instruments as instruments
        r = instruments.InstrumentsCandles(instrument=inst, params={"count": count, "granularity": gran, "price": "M"})
        self.oanda.client.request(r)
        out = []
        for c in r.response.get("candles", []):
            done = c.get("complete", True)
            if not done and not include_live: continue
            m = c.get("mid", {})
            out.append({"time": c["time"], "open": float(m["o"]), "high": float(m["h"]), "low": float(m["l"]),
                        "close": float(m["c"]), "volume": int(c.get("volume", 0)), "complete": done})
        return out

    def prices(self, max_age=20):
        now = time.time()
        if self._prices and now - self._prices["_t"] < max_age:
            return self._prices["data"]
        import oandapyV20.endpoints.pricing as pricing
        r = pricing.PricingInfo(accountID=self.oanda.account_id, params={"instruments": ",".join(BOARD_INSTRUMENTS)})
        self.oanda.client.request(r)
        data = {}
        for p in r.response.get("prices", []):
            try:
                bid, ask = float(p["bids"][0]["price"]), float(p["asks"][0]["price"])
            except (KeyError, IndexError, ValueError):
                continue
            inst = p["instrument"]; spread = ask - bid
            hist = self._spreads.setdefault(inst, deque(maxlen=300))
            med = sorted(hist)[len(hist) // 2] if len(hist) >= 10 else None
            hist.append(spread)
            data[inst] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "spread": spread,
                          "spreadRatio": round(spread / med, 2) if med else None,
                          "tradeable": p.get("tradeable", p.get("status") == "tradeable"), "time": p.get("time")}
        self._prices = {"_t": now, "data": data, "at": datetime.now(timezone.utc).isoformat()}
        return data

    def position_book(self, inst, max_age=3600):
        hit = self._books.get(inst)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        val = None
        try:
            import oandapyV20.endpoints.instruments as instruments
            r = instruments.InstrumentsPositionBook(instrument=inst)
            self.oanda.client.request(r)
            buckets = (r.response.get("positionBook") or {}).get("buckets", [])
            lg = sum(float(b.get("longCountPercent", 0)) for b in buckets)
            sh = sum(float(b.get("shortCountPercent", 0)) for b in buckets)
            if lg + sh > 0:
                val = {"longPct": round(lg / (lg + sh) * 100), "time": (r.response.get("positionBook") or {}).get("time")}
        except Exception as e:
            logger.info(f"[live] position book {inst} unavailable: {e}")
        self._books[inst] = (time.time(), val)
        return val

    # -- one market --
    def _one(self, inst, price):
        try:
            h4 = self.candles(inst, "H4", 320)
            d1 = self.candles(inst, "D", 260, include_live=True)
            h1 = self.candles(inst, "H1", 80)
            res = analyse(h4, d1, h1, price=price, dp=self.dps.get(inst, 5))
        except Exception as e:
            logger.warning(f"[live] {inst} failed: {e}")
            return {"ok": False, "error": str(e)[:200]}
        if inst in POSITION_BOOK:
            res["retail"] = self.position_book(inst)
        if res.get("ok"):
            res["grade"] = grade(res["read"])
        return res

    def board(self, refresh=False, min_age=60):
        with self._board_lock:
            b = self._board
            last_close, next_close = h4_boundaries()
            fresh_enough = b and (time.time() - b["_t"] < min_age or (not refresh and b["candleClose"] == last_close.isoformat()))
            if fresh_enough:
                return b["data"]
            try:
                px = self.prices(max_age=5)
            except Exception as e:
                logger.warning(f"[live] prices failed: {e}"); px = {}
            futs = {i: self._pool.submit(self._one, i, (px.get(i) or {}).get("mid")) for i in BOARD_INSTRUMENTS}
            markets = {i: f.result() for i, f in futs.items()}
            for i, m in markets.items():
                if i in px: m["quote"] = px[i]
            strength = currency_strength({p: (markets.get(p) or {}).get("change24hPct") for p in FX_PAIRS})
            data = {
                "kind": "otm-board", "v": 1, "computedAt": datetime.now(timezone.utc).isoformat(),
                "candleClose": last_close.isoformat(), "nextCandleClose": next_close.isoformat(),
                "timeframe": "H4", "markets": markets, "strength": strength,
                "notes": "Indicator readings are open-source equivalents of the trader's TradingView set-up (RMVP/MCDX/Bj/VP-SR are private scripts).",
            }
            self._board = {"_t": time.time(), "candleClose": last_close.isoformat(), "data": data}
            return data
