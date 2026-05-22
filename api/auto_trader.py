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
MIN_CONFIDENCE   = 60       # Only alert signals above this %
RISK_PERCENT     = 1.0      # 1% risk per trade
EXPIRY_MINUTES   = 15       # Cancel pending trade after this many minutes
MIN_MARGIN       = 20.0     # Minimum free margin in account currency
MAX_OPEN_TRADES  = 3        # Hard limit on simultaneous positions

# ── State ─────────────────────────────────────────────────────────────────────
# Tracks last known signal per instrument to detect NEW signals
_last_signals: dict = {}

# Pending trades waiting for YES/NO reply
# { instrument: { ...trade details, expires_at, status } }
_pending_trades: dict = {}

# Track last Telegram update_id to avoid processing same message twice
_last_update_id: int = 0


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

        # 6. Daily loss limit
        pnl = oanda_client.get_daily_pnl()
        if pnl.get("daily_breached"):
            return False, f"Daily loss limit reached. Lost ${abs(pnl.get('total_pnl',0)):.2f} today"

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


def calculate_units(balance: float, atr: float, sig: dict) -> int:
    """
    ATR-based position sizing.
    Risk = 1% of balance
    Stop = 1.5 x ATR
    Units = Risk / Stop distance
    """
    if not balance or not atr or atr <= 0:
        return 0

    risk_amount   = balance * (RISK_PERCENT / 100)
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