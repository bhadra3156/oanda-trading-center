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
                result = json.loads(r.read())
                return result.get("ok", False)
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def get_updates(self, offset: int = 0) -> list:
        """
        Poll Telegram for new messages.
        Returns list of update objects.
        offset = last_update_id + 1 to avoid reprocessing.
        """
        if not self.token:
            return []
        try:
            params = urllib.parse.urlencode({
                "offset":          offset,
                "timeout":         5,
                "allowed_updates": '["message"]',
            })
            url = f"https://api.telegram.org/bot{self.token}/getUpdates?{params}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
                if data.get("ok"):
                    return data.get("result", [])
        except Exception as e:
            logger.error(f"Telegram getUpdates error: {e}")
        return []

    def send_signal_alert(self, inst: str, sig: dict, ai: dict, units: int) -> bool:
        """Send trade alert with full quality scorecard (Fix 7)."""
        if self._paused:
            return False

        from datetime import datetime, timedelta, timezone
        direction  = sig.get("signal", "")
        ai_verdict = ai.get("verdict", "N/A") if ai else "N/A"
        ai_summary = ai.get("summary", "N/A") if ai else "N/A"
        confidence = sig.get("confidence", 0)
        session    = sig.get("session", "")
        expires    = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%H:%M UTC")

        # R:R calculation
        sl = sig.get("sl", 0); tp = sig.get("tp", 0); entry = sig.get("entry", 0)
        rr = "N/A"
        if sl and tp and entry:
            sd = abs(float(entry) - float(sl))
            td = abs(float(tp)    - float(entry))
            if sd > 0: rr = f"{td/sd:.1f}:1"

        # Quality scorecard gates
        conf_score    = sig.get("confluence", 0)
        conf_label    = sig.get("confluence_label", "NONE")
        weekly_bias   = sig.get("weekly_bias", "NEUTRAL")
        counter_trend = sig.get("counter_trend", False)
        groups        = sig.get("groups_confirmed", 0)
        spread_ratio  = sig.get("spread_info", {}).get("ratio", 1.0)

        gates = 0
        lines = []

        if conf_score >= 2:
            gates += 1; lines.append(f"Confluence: {conf_label} ({conf_score}/3) OK")
        else:
            lines.append(f"Confluence: WEAK ({conf_score}/3) FAIL")

        if not counter_trend:
            gates += 1; lines.append(f"Weekly: {weekly_bias} aligned OK")
        else:
            lines.append(f"Weekly: COUNTER-TREND FAIL")

        if groups >= 3:
            gates += 1; lines.append(f"Indicators: {groups}/5 groups OK")
        else:
            lines.append(f"Indicators: {groups}/5 groups FAIL")

        if spread_ratio < 2.5:
            gates += 1; lines.append(f"Spread: {spread_ratio:.1f}x normal OK")
        else:
            lines.append(f"Spread: {spread_ratio:.1f}x WIDE FAIL")

        if confidence >= 65:
            gates += 1; lines.append(f"Confidence: {confidence}% OK")
        else:
            lines.append(f"Confidence: {confidence}% marginal")

        quality = "STRONG" if gates >= 5 else "GOOD" if gates >= 4 else "MARGINAL" if gates >= 3 else "WEAK"
        gates_text = "
".join(f"  {l}" for l in lines)

        msg = (
            f"*{direction} — {inst.replace('_', '/')}*
"
            f"H4 | {session} | {datetime.now(timezone.utc).strftime('%H:%M UTC')}
"
            f"---
"
            f"Entry:      `{sig.get('entry','—')}`
"
            f"Stop Loss:  `{sig.get('sl','—')}`
"
            f"TP:         `{sig.get('tp','—')}`
"
            f"Size:       `{units:,} units`
"
            f"R:R:        `{rr}`
"
            f"---
"
            f"*QUALITY: {quality} ({gates}/5 gates)*
"
            f"{gates_text}
"
            f"---
"
            f"*AI:* {ai_verdict}
"
            f"_{ai_summary}_
"
            f"---
"
            f"*YES to execute | NO to skip*
"
            f"_Expires: {expires}_"
        )
        return self.send(msg)

    def send_news_block(self, inst: str, sig: dict, blackout: dict) -> bool:
        """
        Notify when a signal is blocked due to news blackout.
        User is informed WHY and WHEN trading resumes.
        """
        direction = sig.get("signal", "")
        confidence= sig.get("confidence", 0)

        # Get blackout details
        active = blackout.get("active_blackouts", [])
        event_title   = active[0].get("title", "Unknown event") if active else "High impact news"
        event_time    = active[0].get("time_utc", "—")          if active else "—"
        minutes_away  = active[0].get("minutes_away", 0)         if active else 0

        # Calculate resume time (event time + 30 min buffer)
        try:
            from datetime import datetime, timedelta, timezone
            now     = datetime.now(timezone.utc)
            resume  = now + timedelta(minutes=abs(minutes_away) + 30)
            resume_str = resume.strftime("%H:%M UTC")
        except Exception:
            resume_str = "after the event"

        msg = (
            f"*NEWS BLACKOUT — {inst.replace('_','/')} {direction} blocked*\n"
            f"---\n"
            f"Event:     {event_title}\n"
            f"Time:      `{event_time}`\n"
            f"Minutes:   {abs(int(minutes_away))} min {'before' if minutes_away > 0 else 'after'}\n"
            f"---\n"
            f"Signal:    {direction} @ {sig.get('entry','—')}\n"
            f"Confidence:{confidence}%\n"
            f"---\n"
            f"Trading resumes: *{resume_str}*\n"
            f"_Signal will be re-evaluated on next scan_"
        )
        return self.send(msg)

    def send_trade_blocked(self, inst: str, direction: str, reason: str) -> bool:
        """Notify when a trade is blocked by safety rules."""
        msg = (
            f"*TRADE BLOCKED — {inst.replace('_','/')} {direction}*\n"
            f"Reason: {reason}\n"
            f"_No action taken_"
        )
        return self.send(msg)

    def send_expired(self, trade: dict) -> bool:
        """Notify when a pending trade expires without confirmation."""
        inst = trade.get("instrument", "").replace("_", "/")
        msg  = (
            f"*Trade expired — {inst}*\n"
            f"{trade.get('direction')} signal not confirmed\n"
            f"Expired after 15 minutes\n"
            f"_No trade placed_"
        )
        return self.send(msg)

    def send_signal(self, instrument: str, sig: dict, ai: dict) -> bool:
        """Standard signal notification (non-auto-trade version)."""
        direction  = sig.get("signal", "")
        arrow      = "UP" if direction == "BUY" else "DN"
        ai_verdict = ai.get("verdict", "N/A") if ai else "N/A"
        provider   = ai.get("provider", "groq").upper() if ai else "GROQ"
        cached     = " [cached]" if ai and ai.get("cached") else ""
        reasons    = "\n".join(
            f"  - {r}" for r in sig.get("reasons", [])[:3]
        )
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
        msg = (
            f"*TRADE PLACED -- {instrument.replace('_', '/')}*\n"
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
            f"Risk of forced closure. Review positions.\n"
            f"_Oanda Trading Center_"
        )
        return self.send(msg)

    def send_startup(self) -> bool:
        msg = (
            f"*Oanda Trading Center Started*\n"
            f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Mode: Semi-Auto (reply YES/NO to signals)\n"
            f"Instruments: GBP/JPY, EUR/USD, XAU/USD,\n"
            f"SUGAR, WHEAT, SPX500, WTI, NATGAS\n"
            f"Confidence threshold: 60%+\n"
            f"Risk per trade: 1%\n"
            f"Expiry: 15 minutes\n"
            f"---\n"
            f"Commands:\n"
            f"YES — confirm pending trade\n"
            f"NO — skip pending trade\n"
            f"STATUS — show pending trades\n"
            f"STOP — pause auto alerts\n"
            f"RESUME — resume auto alerts\n"
            f"_Ready_"
        )
        return self.send(msg)

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    @property
    def is_paused(self):
        return self._paused