"""Telegram Bot Alerts"""
import os, json, logging, urllib.request
from datetime import datetime
logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def send(self, message: str) -> bool:
        if not self.token or not self.chat_id:
            return False
        try:
            payload = {
                "chat_id":    self.chat_id,
                "text":       message,
                "parse_mode": "Markdown",
            }
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                result = json.loads(r.read())
                return result.get("ok", False)
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    def send_signal(self, instrument: str, sig: dict, ai: dict) -> bool:
        direction = sig.get("signal", "")
        arrow     = "UP" if direction == "BUY" else "DN"
        ai_verdict = ai.get("verdict", "N/A") if ai else "N/A"
        reasons   = "\n".join(
            f"  - {r}" for r in sig.get("reasons", [])[:3]
        )
        provider  = ai.get("provider", "groq").upper() if ai else "N/A"
        cached    = " [cached]" if ai and ai.get("cached") else ""

        msg = (
            f"*{arrow} {direction} -- {instrument.replace('_', '/')}*\n"
            f"H4 | {sig.get('session', '--')} | "
            f"{datetime.utcnow().strftime('%H:%M UTC')}\n"
            f"---\n"
            f"Entry:       `{sig.get('entry', '--')}`\n"
            f"Stop Loss:   `{sig.get('sl', '--')}` "
            f"_{sig.get('sl_pips', '--')}p_\n"
            f"Take Profit: `{sig.get('tp', '--')}` "
            f"_{sig.get('tp_pips', '--')}p_\n"
            f"Confidence:  {sig.get('confidence', 0)}%\n"
            f"---\n"
            f"{reasons}\n"
            f"---\n"
            f"*{provider} AI{cached}:* {ai_verdict}\n"
            f"_{ai.get('summary', 'N/A') if ai else 'N/A'}_\n"
            f"---\n"
            f"_Open Trading Center to place trade_"
        )
        return self.send(msg)

    def send_trade_confirmation(
        self, instrument, direction, entry, sl, tp, units
    ) -> bool:
        status = "TRADE PLACED"
        msg = (
            f"*{status} -- {instrument.replace('_', '/')}*\n"
            f"Direction: *{direction}*\n"
            f"Units: `{units:,}`\n"
            f"Entry: `{entry}`\n"
            f"Stop Loss: `{sl}`\n"
            f"Take Profit: `{tp}`\n"
            f"_Via Oanda Trading Center_"
        )
        return self.send(msg)

    def send_margin_warning(self, margin_pct: float, margin_avail: float) -> bool:
        msg = (
            f"*MARGIN WARNING*\n"
            f"Margin available: `${margin_avail:.2f}` "
            f"({margin_pct:.1f}% of NAV)\n"
            f"Risk of forced closure. Close a position immediately.\n"
            f"_Oanda Trading Center_"
        )
        return self.send(msg)

    def send_startup(self) -> bool:
        msg = (
            f"*Oanda Trading Center Started*\n"
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Signal Engine: Active\n"
            f"AI: GROQ + Claude fallback\n"
            f"Instruments: 8 (focused universe)\n"
            f"Scanning: GBP/JPY, EUR/USD, XAU/USD,\n"
            f"SUGAR, WHEAT, SPX500, WTI, NATGAS\n"
            f"_Ready to scan_"
        )
        return self.send(msg)