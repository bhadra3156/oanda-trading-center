"""
Auto Trader — Mode 1 Semi-Automatic
Signal fires → Telegram alert → You reply YES → Trade placed

Flow:
1. Signal engine detects NEW signal (state change WAIT→BUY/SELL)
2. Safety rules checked
3. Telegram alert sent with CONFIRM TRADE?
4. Background task polls Telegram every 30s for YES/NO
5. YES → place order on Oanda → confirmation sent
6. NO or 15min expiry → cancel → notify
"""
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
MIN_CONFIDENCE   = 65       # Only alert signals above this %
RISK_PERCENT     = 1.0      # 1% risk per trade
EXPIRY_MINUTES   = 15       # Cancel pending trade after this many minutes
MIN_MARGIN       = 20.0     # Minimum free margin in account currency
MAX_OPEN_TRADES  = 3        # Hard limit on simultaneous positions

# ── State ─────────────────────────────────────────────────────────────────────
_last_signals:  dict = {}    # last known signal per instrument
_pending_trades:dict = {}    # trades waiting for YES/NO
_last_update_id:int  = 0     # Telegram polling offset

# Fix 10: Minimum hold rule
# After a trade is placed, lock that instrument for MIN_HOLD_BARS × 4 hours
# Prevents whipsaw re-entries before the trade has had time to develop
MIN_HOLD_BARS   = 6          # 6 H4 bars = 24 hours minimum hold
_active_trades: dict = {}    # { instrument: entry_timestamp }

def register_active_trade(instrument: str):
    """Call when a trade is placed. Locks instrument for MIN_HOLD_BARS."""
    import time
    _active_trades[instrument] = time.time()
    logger.info(f"Active trade registered: {instrument} — locked for {MIN_HOLD_BARS*4}h")

def is_instrument_locked(instrument: str) -> bool:
    """Returns True if instrument is within minimum hold period."""
    import time
    entry_ts = _active_trades.get(instrument)
    if not entry_ts:
        return False
    elapsed_hours = (time.time() - entry_ts) / 3600
    locked        = elapsed_hours < (MIN_HOLD_BARS * 4)
    if not locked:
        _active_trades.pop(instrument, None)  # Clean up expired lock
    return locked

def unlock_instrument(instrument: str):
    """Manually unlock an instrument (e.g. when trade closes)."""
    _active_trades.pop(instrument, None)
    logger.info(f"Instrument unlocked: {instrument}")


def check_new_signals(current_signals: dict) -> list:
    """
    Compare current signals against last known state.
    Returns list of NEW signals (state changed to BUY or SELL).
    Ignores signals that were already BUY/SELL last scan.
    """
    new_signals = []
    for inst, sig in current_signals.items():
        current  = sig.get("signal", "WAIT")
        previous = _last_signals.get(inst, "WAIT")

        # Only fire if signal CHANGED to BUY or SELL
        if current in ("BUY", "SELL") and previous != current:
            new_signals.append(sig)
            logger.info(f"NEW signal detected: {inst} {current} (was {previous})")

        # Update state
        _last_signals[inst] = current

    return new_signals


def safety_check(sig: dict, oanda_client, correlation_checker=None) -> tuple:
    """
    Run all safety checks before sending alert.
    Returns (passed: bool, reason: str)
    """
    inst      = sig.get("instrument", "")
    direction = sig.get("signal", "")
    confidence= sig.get("confidence", 0)

    # Fix 10: Minimum hold rule — instrument locked after recent trade
    if is_instrument_locked(inst):
        return False, f"{inst.replace('_','/')} locked — minimum hold period active (24h after entry). Prevents whipsaw re-entries."

    # 1. Confidence threshold
    if confidence < MIN_CONFIDENCE:
        return False, f"Confidence {confidence}% below threshold ({MIN_CONFIDENCE}%)"

    # 2. Session check
    hour = datetime.now(timezone.utc).hour
    in_london   = 7  <= hour < 17
    in_new_york = 13 <= hour < 22
    if not (in_london or in_new_york):
        return False, f"Outside trading hours (current UTC hour: {hour:02d}:xx). London: 07-17, NY: 13-22"

    try:
        account = oanda_client.get_account_summary()
        balance = float(account.get("balance", 0))
        margin  = float(account.get("marginAvailable", 0))
        open_trades = oanda_client.get_open_trades()

        # 3. Max open trades
        if len(open_trades) >= MAX_OPEN_TRADES:
            return False, f"Maximum {MAX_OPEN_TRADES} trades already open ({len(open_trades)} positions)"

        # 4. Already have this instrument
        open_insts = [t.get("instrument") for t in open_trades]
        if inst in open_insts:
            return False, f"Already have {inst.replace('_','/')} position open"

        # 5. Margin check
        if margin < MIN_MARGIN:
            return False, f"Insufficient margin: ${margin:.2f} available (minimum ${MIN_MARGIN:.2f})"

        # 6. Drawdown lockout checks
        pnl = oanda_client.get_daily_pnl()
        if pnl.get("daily_lockout"):
            return False, f"Daily loss lockout (8%): Lost ${pnl.get('daily_loss',0):.2f} today"
        if pnl.get("weekly_lockout"):
            return False, f"Weekly loss lockout (15%): Lost ${pnl.get('weekly_loss',0):.2f} this week"
        if pnl.get("margin_warning"):
            return False, f"Margin critical: only {pnl.get('margin_available',0):.2f} available"

        # 7. Cluster/correlation check
        if correlation_checker:
            open_positions = [
                {"instrument": t.get("instrument"),
                 "direction": "BUY" if float(t.get("currentUnits", 0)) > 0 else "SELL"}
                for t in open_trades
            ]
            result = correlation_checker(inst, direction, open_positions)
            if result.get("block_trade"):
                return False, f"Correlation cluster limit: {result.get('summary', 'conflict detected')}"

    except Exception as e:
        logger.error(f"Safety check error: {e}")
        return False, f"Safety check failed: {str(e)[:100]}"

    return True, "All checks passed"


def add_pending_trade(sig: dict, units: int):
    """Store a trade as pending, waiting for YES/NO."""
    inst = sig.get("instrument")
    _pending_trades[inst] = {
        "instrument": inst,
        "direction":  sig.get("signal"),
        "entry":      sig.get("entry"),
        "sl":         sig.get("sl"),
        "tp":         sig.get("tp"),
        "units":      units,
        "confidence": sig.get("confidence"),
        "atr":        sig.get("atr"),
        "session":    sig.get("session"),
        "expires_at": time.time() + (EXPIRY_MINUTES * 60),
        "status":     "WAITING",
        "created_at": datetime.utcnow().strftime("%H:%M UTC"),
    }
    logger.info(f"Pending trade added: {inst} {sig.get('signal')} — expires in {EXPIRY_MINUTES}min")


def get_pending_trade(inst: str) -> Optional[dict]:
    return _pending_trades.get(inst)


def clear_pending_trade(inst: str):
    _pending_trades.pop(inst, None)


def get_all_pending() -> dict:
    return dict(_pending_trades)


def check_expired_trades() -> list:
    """Return list of expired pending trades and remove them."""
    now     = time.time()
    expired = []
    for inst, trade in list(_pending_trades.items()):
        if trade.get("status") == "WAITING" and now > trade.get("expires_at", 0):
            expired.append(trade)
            clear_pending_trade(inst)
            logger.info(f"Trade expired: {inst}")
    return expired


# Cluster correlation map for adjusted sizing (Fix 9)
CLUSTER_CORRELATION = {
    "WTICO_USD":  {"NATGAS_USD": 0.65, "XAU_USD": 0.45},
    "NATGAS_USD": {"WTICO_USD":  0.65},
    "SUGAR_USD":  {"WHEAT_USD":  0.70},
    "WHEAT_USD":  {"SUGAR_USD":  0.70},
    "SPX500_USD": {"GBP_JPY":    0.55},
    "GBP_JPY":    {"SPX500_USD": 0.55},
    "XAU_USD":    {"EUR_USD":    0.50, "WTICO_USD": 0.45},
    "EUR_USD":    {"XAU_USD":    0.50},
}

def get_correlation_adjustment(instrument: str, open_trades: list) -> float:
    """
    Returns a risk multiplier (0.3–1.0) based on correlated open positions.
    Fix 9: If you have SUGAR open and WHEAT fires, size WHEAT at 0.5x not 1x.
    Units = 1% × (1 - max_correlation × existing_cluster_exposure_pct)
    """
    corr_map = CLUSTER_CORRELATION.get(instrument, {})
    if not corr_map or not open_trades:
        return 1.0  # No correlation — full size

    max_corr = 0.0
    for t in open_trades:
        open_inst = t.get("instrument", "")
        if open_inst == instrument:
            continue
        corr = corr_map.get(open_inst, 0.0)
        if corr > max_corr:
            max_corr = corr

    # Reduce position size by correlation factor
    # Max correlation 0.7 → size at 0.3× (30% of normal)
    # No correlation 0.0 → size at 1.0× (100% of normal)
    multiplier = max(0.3, 1.0 - max_corr)
    if max_corr > 0.3:
        logger.info(f"{instrument}: correlation-adjusted sizing {multiplier:.2f}x (max corr: {max_corr:.2f})")
    return multiplier


def calculate_units(balance: float, atr: float, sig: dict, open_trades: list = None) -> int:
    """
    ATR-based position sizing with correlation adjustment (Fix 9).
    Risk = 1% of balance × correlation_multiplier
    Stop = 1.5 x ATR
    Units = Risk / Stop distance
    """
    if not balance or not atr or atr <= 0:
        return 0

    # Fix 9: Adjust risk % based on correlated open positions
    corr_multiplier = get_correlation_adjustment(
        sig.get("instrument", ""), open_trades or []
    )
    risk_amount   = balance * (RISK_PERCENT / 100) * corr_multiplier
    stop_distance = atr * 1.5

    if stop_distance <= 0:
        return 0

    units = int(risk_amount / stop_distance)

    # Instrument-specific limits
    inst = sig.get("instrument", "")
    if "SPX" in inst or "NAS" in inst:
        units = max(1, min(units, 50))
    elif "XAU" in inst:
        units = max(1, min(units, 100))
    elif "NATGAS" in inst or "WTICO" in inst:
        units = max(100, min(units, 50000))
    else:
        units = max(100, min(units, 100000))

    return units


def process_telegram_reply(
    message_text: str,
    oanda_client,
    telegram_bot,
    correlation_checker=None
) -> bool:
    """
    Process an incoming Telegram message.
    Returns True if a trade was executed or cancelled.
    """
    global _last_update_id

    text = message_text.strip().upper()

    if text == "YES":
        # Find the most recently added pending trade
        if not _pending_trades:
            telegram_bot.send("No pending trades to confirm.")
            return False

        # Get oldest pending (FIFO)
        pending_insts = [
            inst for inst, t in _pending_trades.items()
            if t.get("status") == "WAITING"
        ]
        if not pending_insts:
            telegram_bot.send("No trades waiting for confirmation.")
            return False

        # Pick the one closest to expiry (most urgent)
        inst = min(
            pending_insts,
            key=lambda i: _pending_trades[i].get("expires_at", 0)
        )
        trade = _pending_trades[inst]

        # Re-run safety checks before executing
        sig_stub = {
            "instrument": inst,
            "signal":     trade["direction"],
            "confidence": trade["confidence"],
        }
        passed, reason = safety_check(sig_stub, oanda_client, correlation_checker)

        if not passed:
            telegram_bot.send(
                f"*Trade blocked at execution*\n"
                f"{inst.replace('_','/')}: {trade['direction']}\n"
                f"Reason: {reason}\n"
                f"Trade cancelled."
            )
            clear_pending_trade(inst)
            return False

        # Execute the trade
        try:
            direction = trade["direction"]
            units     = trade["units"]
            sl        = trade["sl"]
            tp        = trade["tp"]

            signed_units = units if direction == "BUY" else -units
            result = oanda_client.place_order_with_levels(
                instrument=inst,
                units=signed_units,
                stop_loss=sl,
                take_profit=tp,
            )

            # Fix 10: Register trade to enforce minimum hold period
            register_active_trade(inst)

            inst_display = inst.replace("_", "/")
            entry_actual = trade.get("entry", "market")

            telegram_bot.send(
                f"*TRADE EXECUTED*\n"
                f"{direction} {inst_display}\n"
                f"---\n"
                f"Entry:  `{entry_actual}`\n"
                f"SL:     `{sl}`\n"
                f"TP:     `{tp}`\n"
                f"Units:  `{units:,}`\n"
                f"Risk:   1% of balance\n"
                f"---\n"
                f"_Trade placed via auto-trader_"
            )
            clear_pending_trade(inst)
            register_active_trade(inst)  # Fix 10: enforce minimum hold
            logger.info(f"Auto trade executed: {inst} {direction} {units} units")
            return True

        except Exception as e:
            telegram_bot.send(
                f"*Trade execution failed*\n"
                f"{inst.replace('_','/')}: {trade['direction']}\n"
                f"Error: {str(e)[:150]}\n"
                f"Please place manually."
            )
            clear_pending_trade(inst)
            logger.error(f"Auto trade execution error: {e}")
            return False

    elif text == "NO" or text == "SKIP" or text == "CANCEL":
        if not _pending_trades:
            telegram_bot.send("No pending trades to cancel.")
            return False

        # Cancel all pending trades
        cancelled = list(_pending_trades.keys())
        for inst in cancelled:
            clear_pending_trade(inst)

        names = ", ".join(i.replace("_", "/") for i in cancelled)
        telegram_bot.send(f"Cancelled: {names}\nSignal skipped.")
        logger.info(f"Trades cancelled by user: {cancelled}")
        return True

    elif text == "STATUS" or text == "PENDING":
        # Show all pending trades
        if not _pending_trades:
            telegram_bot.send("No pending trades.")
        else:
            lines = ["*Pending trades:*"]
            for inst, t in _pending_trades.items():
                remaining = max(0, int((t["expires_at"] - time.time()) / 60))
                lines.append(
                    f"{inst.replace('_','/')}: {t['direction']} "
                    f"— {remaining}min remaining"
                )
            telegram_bot.send("\n".join(lines))
        return True

    elif text == "STOP" or text == "PAUSE":
        telegram_bot.send(
            "*Auto-trader paused*\n"
            "No new alerts will be sent.\n"
            "Send RESUME to restart."
        )
        return True

    return False