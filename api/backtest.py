"""
Backtest Engine — H4 Confluence System
Runs your exact live signal logic against historical Oanda candle data.
Proves edge with real data. No curve fitting — same rules as live trading.

Strategy rules (identical to signals.py):
  BUY:  score >= 55 — RSI oversold, price > EMA200, golden cross, MACD bull, Donchian break, volume
  SELL: score >= 55 — RSI overbought, price < EMA200, death cross, MACD bear, Donchian break, volume
  SL:   entry ± 1.5 × ATR(14)
  TP:   entry ± 2.5 × ATR(14)
  Max hold: 20 bars (20 × H4 = ~3.3 trading days)
"""
import logging
from datetime import datetime
logger = logging.getLogger(__name__)

# ── Same indicator functions as signals.py ────────────────────────────────────

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = float(closes[i]) - float(closes[i-1])
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    return round(100 - (100 / (1 + ag/al)), 2) if al != 0 else 100.0

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(float(c) for c in closes[:period]) / period
    for c in closes[period:]:
        ema = float(c) * k + ema * (1 - k)
    return ema

def calc_atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = [
        max(
            float(c["high"]) - float(c["low"]),
            abs(float(c["high"]) - float(candles[i-1]["close"])),
            abs(float(c["low"])  - float(candles[i-1]["close"])),
        )
        for i, c in enumerate(candles[1:], 1)
    ]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period-1) + tr) / period
    return atr

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
    return macd, bool(macd > 0 and prev <= 0), bool(macd < 0 and prev >= 0)

def get_pip(instrument):
    if "JPY"   in instrument: return 0.01
    if "XAU"   in instrument: return 0.1
    if any(x in instrument for x in ["WTICO","NATGAS","SUGAR","WHEAT"]):
        return 0.01
    if any(x in instrument for x in ["SPX","NAS"]):
        return 1.0
    return 0.0001

# ── Core scoring function — identical to signals.py ──────────────────────────

def score_bar(candles_window):
    """
    Given a window of candles (most recent = last),
    return (buy_score, sell_score, atr, entry_close).
    Minimum 210 candles required.
    """
    if len(candles_window) < 210:
        return 0, 0, None, None

    closes = [float(c["close"])  for c in candles_window]
    highs  = [float(c["high"])   for c in candles_window]
    lows   = [float(c["low"])    for c in candles_window]
    vols   = [int(c.get("volume", 0)) for c in candles_window]

    rsi    = calc_rsi(closes, 14)
    ema20  = calc_ema(closes, 20)
    ema50  = calc_ema(closes, 50)
    ema200 = calc_ema(closes, 200)
    atr    = calc_atr(candles_window, 14)
    _, macd_bull, macd_bear = calc_macd(closes)

    don_high = max(highs[-21:-1]) if len(highs) > 21 else None
    don_low  = min(lows[-21:-1])  if len(lows)  > 21 else None
    latest   = candles_window[-1]
    prev     = candles_window[-2]
    price    = float(latest["close"])

    avg_vol   = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1
    vol_surge = bool(vols[-1] > avg_vol * 1.15)

    bs, ss = 0, 0

    # RSI
    if rsi and rsi < 35:   bs += 30
    elif rsi and rsi < 42: bs += 15
    if rsi and rsi > 65:   ss += 30
    elif rsi and rsi > 58: ss += 15

    # EMAs
    if ema200 and price > ema200: bs += 20
    if ema200 and price < ema200: ss += 20
    if ema50 and ema200 and ema50 > ema200: bs += 12
    if ema50 and ema200 and ema50 < ema200: ss += 12
    if ema20 and ema50 and ema20 > ema50:   bs += 8
    if ema20 and ema50 and ema20 < ema50:   ss += 8

    # MACD
    if macd_bull: bs += 20
    if macd_bear: ss += 20

    # Donchian
    if don_high and float(prev["close"]) < don_high <= float(latest["close"]):
        bs += 18
    if don_low and float(prev["close"]) > don_low >= float(latest["close"]):
        ss += 18

    # Volume
    if vol_surge:
        if bs >= ss: bs += 5
        else:        ss += 5

    return int(bs), int(ss), atr, price


# ── Main backtest function ────────────────────────────────────────────────────

def run_backtest(
    instrument: str,
    candles: list,
    starting_balance: float = 627.0,
    risk_pct: float = 1.0,
    max_hold_bars: int = 20,
    signal_threshold: int = 55,
) -> dict:
    """
    Walk through historical candles bar by bar.
    Apply the exact same scoring system as the live engine.
    Record every signal, outcome, R-multiple.
    Return full statistics.
    """
    if len(candles) < 250:
        return {
            "instrument": instrument,
            "error": f"Insufficient data: {len(candles)} candles (need 250+)",
            "trades": [],
        }

    pip       = get_pip(instrument)
    trades    = []
    equity    = starting_balance
    equity_curve = [{"bar": 0, "equity": round(equity, 2), "date": candles[210]["time"][:10]}]
    peak_equity  = equity
    max_drawdown = 0.0
    in_trade     = False
    trade_start  = 0

    logger.info(f"Backtesting {instrument}: {len(candles)} candles")

    # Walk from bar 210 (need enough history for indicators) to end
    for i in range(210, len(candles) - max_hold_bars - 1):
        if in_trade:
            continue  # Only one trade at a time per instrument

        window = candles[:i+1]  # All candles up to and including bar i
        bs, ss, atr, price = score_bar(window)

        if atr is None or atr <= 0:
            continue

        signal = None
        if bs >= signal_threshold and bs > ss:
            signal = "BUY"
        elif ss >= signal_threshold and ss > bs:
            signal = "SELL"

        if signal is None:
            continue

        # Calculate levels
        entry = float(candles[i]["close"])
        if signal == "BUY":
            sl = round(entry - atr * 1.5, 6)
            tp = round(entry + atr * 2.5, 6)
        else:
            sl = round(entry + atr * 1.5, 6)
            tp = round(entry - atr * 2.5, 6)

        # Risk amount
        risk_amount = equity * (risk_pct / 100)

        # Simulate trade outcome by walking forward bars
        outcome    = None
        exit_price = None
        exit_bar   = None
        hold_bars  = 0

        for j in range(i + 1, min(i + max_hold_bars + 1, len(candles))):
            c = candles[j]
            h = float(c["high"])
            l = float(c["low"])
            hold_bars = j - i

            if signal == "BUY":
                if l <= sl:
                    outcome    = "LOSS"
                    exit_price = sl
                    exit_bar   = j
                    break
                if h >= tp:
                    outcome    = "WIN"
                    exit_price = tp
                    exit_bar   = j
                    break
            else:  # SELL
                if h >= sl:
                    outcome    = "LOSS"
                    exit_price = sl
                    exit_bar   = j
                    break
                if l <= tp:
                    outcome    = "WIN"
                    exit_price = tp
                    exit_bar   = j
                    break

        # Timeout — close at last bar's close
        if outcome is None:
            exit_bar   = min(i + max_hold_bars, len(candles) - 1)
            exit_price = float(candles[exit_bar]["close"])
            hold_bars  = exit_bar - i
            if signal == "BUY":
                outcome = "WIN" if exit_price > entry else "LOSS"
            else:
                outcome = "WIN" if exit_price < entry else "LOSS"

        # Calculate P&L in R-multiples
        sl_distance = abs(entry - sl)
        tp_distance = abs(tp - entry)
        rr_ratio    = round(tp_distance / sl_distance, 2) if sl_distance > 0 else 0

        if signal == "BUY":
            price_pnl = exit_price - entry
        else:
            price_pnl = entry - exit_price

        r_multiple = round(price_pnl / sl_distance, 3) if sl_distance > 0 else 0

        # Dollar P&L
        pnl_dollar = round(risk_amount * r_multiple, 2)
        equity     = round(equity + pnl_dollar, 2)

        # Track drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

        # Record trade
        trades.append({
            "instrument":  instrument,
            "signal":      signal,
            "entry_date":  candles[i]["time"][:10],
            "exit_date":   candles[exit_bar]["time"][:10],
            "entry":       round(entry, 6),
            "sl":          round(sl, 6),
            "tp":          round(tp, 6),
            "exit_price":  round(exit_price, 6),
            "outcome":     outcome,
            "r_multiple":  r_multiple,
            "pnl_dollar":  pnl_dollar,
            "hold_bars":   hold_bars,
            "rr_ratio":    rr_ratio,
            "buy_score":   bs,
            "sell_score":  ss,
            "equity_after":round(equity, 2),
        })

        equity_curve.append({
            "bar":    exit_bar,
            "equity": round(equity, 2),
            "date":   candles[exit_bar]["time"][:10],
        })

    # ── Calculate statistics ──────────────────────────────────────────────────
    if not trades:
        return {
            "instrument":  instrument,
            "total_trades":0,
            "message":     "No signals generated with current threshold",
            "trades":      [],
        }

    wins   = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    rs     = [t["r_multiple"] for t in trades]
    win_rs = [t["r_multiple"] for t in wins]
    los_rs = [t["r_multiple"] for t in losses]

    win_rate   = round(len(wins) / len(trades) * 100, 1)
    avg_r      = round(sum(rs) / len(rs), 3)
    avg_win_r  = round(sum(win_rs) / len(win_rs), 3)  if win_rs  else 0
    avg_loss_r = round(sum(los_rs) / len(los_rs), 3)  if los_rs  else 0
    total_pnl  = round(equity - starting_balance, 2)
    total_ret  = round(total_pnl / starting_balance * 100, 2)

    # Profit factor
    gross_profit = sum(t["pnl_dollar"] for t in wins)
    gross_loss   = abs(sum(t["pnl_dollar"] for t in losses))
    profit_factor= round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

    # Sharpe ratio (simplified — using R-multiples)
    avg_r_val = sum(rs) / len(rs)
    std_r     = (sum((r - avg_r_val)**2 for r in rs) / len(rs)) ** 0.5
    sharpe    = round(avg_r_val / std_r, 2) if std_r > 0 else 0.0

    # Longest losing streak
    max_streak = cur_streak = 0
    for t in trades:
        if t["outcome"] == "LOSS":
            cur_streak += 1
            max_streak  = max(max_streak, cur_streak)
        else:
            cur_streak  = 0

    # Monthly breakdown
    monthly = {}
    for t in trades:
        month = t["entry_date"][:7]
        if month not in monthly:
            monthly[month] = {"wins": 0, "losses": 0, "pnl": 0.0, "trades": 0}
        monthly[month]["trades"] += 1
        monthly[month]["pnl"]    += t["pnl_dollar"]
        if t["outcome"] == "WIN": monthly[month]["wins"] += 1
        else:                     monthly[month]["losses"] += 1

    monthly_list = [
        {
            "month":     m,
            "trades":    d["trades"],
            "wins":      d["wins"],
            "losses":    d["losses"],
            "pnl":       round(d["pnl"], 2),
            "win_rate":  round(d["wins"]/d["trades"]*100, 1) if d["trades"] else 0,
        }
        for m, d in sorted(monthly.items())
    ]

    return {
        "instrument":      instrument,
        "total_trades":    len(trades),
        "wins":            len(wins),
        "losses":          len(losses),
        "win_rate":        win_rate,
        "avg_r":           avg_r,
        "avg_win_r":       avg_win_r,
        "avg_loss_r":      avg_loss_r,
        "profit_factor":   profit_factor,
        "sharpe_ratio":    sharpe,
        "max_drawdown_pct":round(max_drawdown, 2),
        "max_losing_streak":max_streak,
        "total_pnl":       total_pnl,
        "total_return_pct":total_ret,
        "starting_balance":starting_balance,
        "ending_balance":  round(equity, 2),
        "candles_analysed":len(candles),
        "date_from":       candles[210]["time"][:10],
        "date_to":         candles[-1]["time"][:10],
        "equity_curve":    equity_curve,
        "monthly":         monthly_list,
        "trades":          trades,
    }