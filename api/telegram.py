"""
Telegram Bot — Alerts + Reply Polling for Semi-Auto Trading
"""
import os, json, logging, urllib.request, urllib.parse
from datetime import datetime
logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self._paused = False

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
                return r.status == 200
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def get_updates(self, offset: int = 0) -> list:
        if not self.token:
            return []
        try:
            url = (
                f"https://api.telegram.org/bot{self.token}/getUpdates"
                f"?offset={offset}&timeout=5&limit=10"
            )
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.loads(r.read())
                return data.get("result", [])
        except Exception as e:
            logger.debug(f"Telegram get_updates error: {e}")
            return []

    def send_signal_alert(self, inst: str, sig: dict, ai: dict, units: int) -> bool:
        """Send trade alert with full quality scorecard."""
        if self._paused:
            return False

        from datetime import timedelta, timezone
        direction  = sig.get("signal", "")
        ai_verdict = ai.get("verdict", "N/A") if ai else "N/A"
        ai_summary = ai.get("summary", "N/A") if ai else "N/A"
        confidence = sig.get("confidence", 0)
        session    = sig.get("session", "")
        now_utc    = datetime.now(timezone.utc)
        expires    = (now_utc + timedelta(minutes=15)).strftime("%H:%M UTC")
        time_str   = now_utc.strftime("%H:%M UTC")

        # R:R calculation
        sl    = sig.get("sl", 0)
        tp    = sig.get("tp", 0)
        entry = sig.get("entry", 0)
        rr    = "N/A"
        if sl and tp and entry:
            sd = abs(float(entry) - float(sl))
            td = abs(float(tp)    - float(entry))
            if sd > 0:
                rr = f"{td/sd:.1f}:1"

        # Quality scorecard gates
        conf_score    = sig.get("confluence", 0)
        conf_label    = sig.get("confluence_label", "NONE")
        weekly_bias   = sig.get("weekly_bias", "NEUTRAL")
        counter_trend = sig.get("counter_trend", False)
        groups        = sig.get("groups_confirmed", 0)
        spread_ratio  = sig.get("spread_info", {}).get("ratio", 1.0)

        gates = 0
        gate_lines = []

        if conf_score >= 2:
            gates += 1
            gate_lines.append(f"Confluence: {conf_label} ({conf_score}/3) OK")
        else:
            gate_lines.append(f"Confluence: WEAK ({conf_score}/3) FAIL")

        if not counter_trend:
            gates += 1
            gate_lines.append(f"Weekly: {weekly_bias} aligned OK")
        else:
            gate_lines.append(f"Weekly: COUNTER-TREND FAIL")

        if groups >= 3:
            gates += 1
            gate_lines.append(f"Indicators: {groups}/5 groups OK")
        else:
            gate_lines.append(f"Indicators: {groups}/5 groups FAIL")

        if spread_ratio < 2.5:
            gates += 1
            gate_lines.append(f"Spread: {spread_ratio:.1f}x normal OK")
        else:
            gate_lines.append(f"Spread: {spread_ratio:.1f}x WIDE FAIL")

        if confidence >= 65:
            gates += 1
            gate_lines.append(f"Confidence: {confidence}% OK")
        else:
            gate_lines.append(f"Confidence: {confidence}% marginal")

        quality = (
            "STRONG"   if gates >= 5 else
            "GOOD"     if gates >= 4 else
            "MARGINAL" if gates >= 3 else
            "WEAK"
        )

        gates_text = "\n".join(f"  {l}" for l in gate_lines)

        inst_display = inst.replace("_", "/")
        entry_str    = str(sig.get("entry", "---"))
        sl_str       = str(sig.get("sl",    "---"))
        tp_str       = str(sig.get("tp",    "---"))
        units_str    = f"{units:,}"

        msg = (
            f"*{direction} -- {inst_display}*\n"
            f"H4 | {session} | {time_str}\n"
            f"---\n"
            f"Entry:      `{entry_str}`\n"
            f"Stop Loss:  `{sl_str}`\n"
            f"TP:         `{tp_str}`\n"
            f"Size:       `{units_str} units`\n"
            f"R:R:        `{rr}`\n"
            f"---\n"
            f"*QUALITY: {quality} ({gates}/5 gates)*\n"
            f"{gates_text}\n"
            f"---\n"
            f"*AI:* {ai_verdict}\n"
            f"_{ai_summary}_\n"
            f"---\n"
            f"*YES to execute | NO to skip*\n"
            f"_Expires: {expires}_"
        )
        return self.send(msg)

    def send_news_block(self, inst: str, sig: dict, news: dict) -> bool:
        """Notify when a signal is blocked by news blackout."""
        inst_display = inst.replace("_", "/")
        event        = news.get("event", "High Impact News")
        resume       = news.get("resume_time", "unknown")
        direction    = sig.get("signal", "")
        confidence   = sig.get("confidence", 0)

        msg = (
            f"*NEWS BLACKOUT -- {inst_display}*\n"
            f"Signal blocked: {direction} at {confidence}% confidence\n"
            f"---\n"
            f"Event:  {event}\n"
            f"Resume: {resume}\n"
            f"---\n"
            f"_Trading resumes automatically after news window_"
        )
        return self.send(msg)

    def send_trade_blocked(self, inst: str, direction: str, reason: str) -> bool:
        """Notify when a trade is blocked by safety checks."""
        inst_display = inst.replace("_", "/")
        msg = (
            f"*TRADE BLOCKED -- {inst_display}*\n"
            f"Direction: {direction}\n"
            f"Reason: {reason}\n"
            f"---\n"
            f"_Safety check prevented this trade_"
        )
        return self.send(msg)

    def send_expired(self, inst: str, direction: str) -> bool:
        """Notify when a pending trade expires without YES/NO reply."""
        inst_display = inst.replace("_", "/")
        msg = (
            f"*TRADE EXPIRED -- {inst_display}*\n"
            f"No reply received for {direction} signal\n"
            f"_Signal expired after 15 minutes_"
        )
        return self.send(msg)

    def send_signal(self, inst: str, sig: dict) -> bool:
        """Simple signal notification (no trade prompt)."""
        inst_display = inst.replace("_", "/")
        direction    = sig.get("signal", "")
        confidence   = sig.get("confidence", 0)
        entry        = sig.get("entry", "---")
        msg = (
            f"*{direction} -- {inst_display}*\n"
            f"Confidence: {confidence}%\n"
            f"Entry: `{entry}`\n"
            f"_Monitor only -- auto-trade threshold not met_"
        )
        return self.send(msg)

    def send_trade_confirmation(self, inst: str, direction: str,
                                 units: int, entry: float,
                                 sl: float, tp: float) -> bool:
        """Confirm a trade was successfully placed."""
        inst_display = inst.replace("_", "/")
        units_str    = f"{units:,}"
        msg = (
            f"*TRADE PLACED -- {inst_display}*\n"
            f"Direction: {direction}\n"
            f"Size:      `{units_str} units`\n"
            f"Entry:     `{entry}`\n"
            f"Stop Loss: `{sl}`\n"
            f"TP:        `{tp}`\n"
            f"Risk:      1% of balance\n"
            f"---\n"
            f"_Trade placed via auto-trader_"
        )
        return self.send(msg)

    def send_margin_warning(self, balance: float, margin: float) -> bool:
        """Warn when margin falls below safe level."""
        msg = (
            f"*MARGIN WARNING*\n"
            f"Available margin: GBP {margin:.2f}\n"
            f"Balance:          GBP {balance:.2f}\n"
            f"---\n"
            f"_Consider closing a position to free margin_"
        )
        return self.send(msg)

    def send_startup(self, env: str, instruments: list) -> bool:
        """Send startup notification."""
        inst_list = ", ".join(i.replace("_", "/") for i in instruments)
        msg = (
            f"*Oanda Trading Center -- ONLINE*\n"
            f"Environment: {env}\n"
            f"Instruments: {inst_list}\n"
            f"H4 confluence system active\n"
            f"_Auto-trader ready_"
        )
        return self.send(msg)

    def pause(self):
        self._paused = True
        logger.info("Telegram bot paused")

    def resume(self):
        self._paused = False
        logger.info("Telegram bot resumed")

    def is_paused(self) -> bool:
        return self._paused
