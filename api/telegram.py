"""Telegram Bot Alerts"""
import os, json, logging, urllib.request
from datetime import datetime
logger = logging.getLogger(__name__)

class TelegramBot:
    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def send(self, message):
        if not self.token or not self.chat_id:
            return False
        try:
            payload = {"chat_id":self.chat_id,"text":message,"parse_mode":"Markdown"}
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data=json.dumps(payload).encode(),
                headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                result = json.loads(r.read())
                return result.get("ok", False)
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False

    def send_signal(self, instrument, sig, ai):
        direction = sig.get("signal","")
        emoji = "🟢" if direction=="BUY" else "🔴"
        ai_emoji = ("🟢🟢" if "STRONG BUY" in ai.get("verdict","") else
                    "🟢"   if "BUY"        in ai.get("verdict","") else
                    "🔴🔴" if "STRONG SELL" in ai.get("verdict","") else
                    "🔴"   if "SELL"       in ai.get("verdict","") else "🟡")
        reasons = "\n".join(f"  • {r}" for r in sig.get("reasons",[])[:3])
        msg = f"""{emoji} *{direction} — {instrument.replace('_','/')}*
⏱ H4 · {sig.get('session','—')} · {datetime.utcnow().strftime('%H:%M UTC')}
━━━━━━━━━━━━━━━━━━━━
📍 *Entry:*       `{sig.get('entry','—')}`
🛑 *Stop Loss:*   `{sig.get('sl','—')}` _{sig.get('sl_pips','—')}p_
🎯 *Take Profit:* `{sig.get('tp','—')}` _{sig.get('tp_pips','—')}p_
📊 *Confidence:*  {sig.get('confidence',0)}%
━━━━━━━━━━━━━━━━━━━━
{reasons}
━━━━━━━━━━━━━━━━━━━━
{ai_emoji} *AI Verdict:* {ai.get('verdict','N/A')}
⚠️ {ai.get('warning','N/A')}
_{ai.get('summary','N/A')}_
━━━━━━━━━━━━━━━━━━━━
_Open Trading Center to place trade_"""
        return self.send(msg)

    def send_trade_confirmation(self, instrument, direction, entry, sl, tp, units):
        emoji = "✅🟢" if direction=="BUY" else "✅🔴"
        msg = f"""{emoji} *TRADE PLACED — {instrument.replace('_','/')}*
Direction: *{direction}*
Units: `{units:,}`
Entry: `{entry}`
Stop Loss: `{sl}`
Take Profit: `{tp}`
_Via Oanda Trading Center_"""
        return self.send(msg)

    def send_startup(self):
        msg = f"""🚀 *Oanda Trading Center Started*
⏱ {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
✅ Signal Engine: Active
✅ Gemini AI: Ready
✅ Telegram: Connected
✅ Supabase: Connected
_Scanning 15 instruments on H4_"""
        return self.send(msg)
