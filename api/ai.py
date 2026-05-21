"""
ai.py — Groq AI Trading Analyst
Free tier: 14,400 requests/day, 100,000 tokens/day
Model: llama-3.3-70b-versatile
"""

import os
import logging

logger = logging.getLogger(__name__)

_groq_client = None


def _get_client():
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                logger.warning("GROQ_API_KEY not set")
                return None
            _groq_client = Groq(api_key=api_key)
        except Exception as e:
            logger.error(f"Groq client init error: {e}")
            return None
    return _groq_client


class GeminiAnalyst:
    """
    Groq-powered analyst — same class name kept so nothing else changes.
    Uses llama-3.3-70b-versatile (free tier).
    Handles rate limits gracefully — never crashes the signal engine.
    """

    # ── SIGNAL ANALYSIS ───────────────────────────────────────────────────────
    def analyse(self, instrument: str, signal_data: dict) -> dict:
        client = _get_client()
        if client is None:
            return self._unavailable("GROQ_API_KEY not set in environment")

        prompt = f"""You are a professional forex and commodities trader.
Analyse this H4 trading signal and give a clear verdict.

Instrument: {instrument.replace('_', '/')}
Signal: {signal_data.get('signal')}
Price: {signal_data.get('price')}
RSI(14): {signal_data.get('rsi')}
Trend: {signal_data.get('trend')}
EMA200: {signal_data.get('ema200')}
ATR: {signal_data.get('atr')}
MACD: {signal_data.get('macd')}
Session: {signal_data.get('session')}
Entry: {signal_data.get('entry')}
Stop Loss: {signal_data.get('sl')}
Take Profit: {signal_data.get('tp')}
Reasons: {', '.join(signal_data.get('reasons', []))}

Reply ONLY in this exact format — no extra text:
VERDICT: [STRONG BUY/BUY/WEAK BUY/WAIT/WEAK SELL/SELL/STRONG SELL]
CONFIDENCE: [0-100]%
KEY_LEVEL: [most important price level]
WARNING: [biggest risk for this trade]
SUMMARY: [2 sentences max in plain English]"""

        try:
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert forex and commodities trading analyst. "
                            "Always reply in the exact structured format requested. "
                            "Be direct, specific and concise. Real money is at stake."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=250,
                temperature=0.2,
            )
            text = completion.choices[0].message.content or ""
            return self._parse(text)

        except Exception as e:
            err = str(e)
            # Handle rate limit gracefully — don't crash, just skip AI
            if "429" in err or "rate_limit" in err or "tokens per day" in err:
                logger.warning(f"Groq rate limited for {instrument} — skipping AI")
                return {
                    "verdict":    "RATE LIMITED",
                    "confidence": 0,
                    "key_level":  "N/A",
                    "warning":    "Groq daily quota reached — resets at midnight UTC",
                    "summary":    "AI analysis unavailable today. Signals are still valid — use technical indicators.",
                    "raw":        err[:200],
                }
            logger.error(f"Groq error for {instrument}: {err[:200]}")
            return self._unavailable(err[:150])

    # ── AI PERFORMANCE DEBRIEF ─────────────────────────────────────────────────
    def analyse_debrief(self, prompt: str) -> str:
        """
        Analyse a trader's recent trade history and return coaching advice.
        Called by /api/ai-debrief endpoint.
        """
        client = _get_client()
        if client is None:
            return "GROQ_API_KEY not set — add it to Render environment variables."

        try:
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert trading performance coach. "
                            "Analyse the trader's history honestly. "
                            "Be direct, specific and actionable. "
                            "Format your response clearly with numbered sections."
                        )
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.4,
            )
            return completion.choices[0].message.content or "No analysis returned."

        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err:
                return (
                    "⚠️ Groq daily quota reached.\n\n"
                    "Your free tier allows 100,000 tokens/day.\n"
                    "AI debrief will be available again after midnight UTC.\n\n"
                    "In the meantime, review your journal manually:\n"
                    "• Which instruments have the highest win rate?\n"
                    "• Are you trading during London/NY sessions only?\n"
                    "• Are you respecting your 1% risk rule?"
                )
            return f"AI debrief unavailable: {err[:200]}"

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _unavailable(self, reason: str = "") -> dict:
        return {
            "verdict":    "UNAVAILABLE",
            "confidence": 0,
            "key_level":  "N/A",
            "warning":    "N/A",
            "summary":    f"AI unavailable: {reason}" if reason else "AI unavailable",
            "raw":        "",
        }

    def _parse(self, text: str) -> dict:
        """Parse structured Groq response into a dict."""
        result = {
            "verdict":    "UNKNOWN",
            "confidence": 0,
            "key_level":  "N/A",
            "warning":    "N/A",
            "summary":    text[:300],
            "raw":        text,
        }
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("VERDICT:"):
                result["verdict"] = line.replace("VERDICT:", "").strip()
            elif line.startswith("CONFIDENCE:"):
                try:
                    result["confidence"] = int(
                        line.replace("CONFIDENCE:", "").replace("%", "").strip()
                    )
                except ValueError:
                    pass
            elif line.startswith("KEY_LEVEL:"):
                result["key_level"] = line.replace("KEY_LEVEL:", "").strip()
            elif line.startswith("WARNING:"):
                result["warning"] = line.replace("WARNING:", "").strip()
            elif line.startswith("SUMMARY:"):
                result["summary"] = line.replace("SUMMARY:", "").strip()
        return result