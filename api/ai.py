"""Groq AI — Free trading analysis (replaces Gemini)"""
import os, logging
logger = logging.getLogger(__name__)

# ── Groq client (lazy-loaded so import never fails if key missing) ──────────
_groq_client = None

def _get_client():
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key:
                return None
            _groq_client = Groq(api_key=api_key)
        except Exception as e:
            logger.error(f"Groq client init error: {e}")
            return None
    return _groq_client


class GeminiAnalyst:
    """
    Drop-in replacement for the original GeminiAnalyst.
    Identical interface — same class name, same .analyse() method,
    same return dict keys — so nothing else in the codebase needs changing.
    Now powered by Groq (llama-3.3-70b-versatile) instead of Gemini.
    """

    def analyse(self, instrument, signal_data):
        client = _get_client()
        if client is None:
            return {
                "verdict":     "UNAVAILABLE",
                "confidence":  0,
                "key_level":   "N/A",
                "warning":     "N/A",
                "summary":     "GROQ_API_KEY not set — add it to .env and Render environment variables",
                "raw":         ""
            }

        prompt = f"""You are a professional forex and commodities trader.
Analyse this H4 trading signal and give a clear verdict.

Instrument: {instrument.replace('_','/')}
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

Reply ONLY in this exact format (no extra text):
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
                            "Be direct, specific, and concise."
                        )
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=250,
                temperature=0.2
            )
            text = completion.choices[0].message.content or ""
            return self._parse(text)

        except Exception as e:
            logger.error(f"Groq error: {e}")
            return {
                "verdict":    "UNAVAILABLE",
                "confidence": 0,
                "key_level":  "N/A",
                "warning":    "N/A",
                "summary":    f"AI unavailable: {str(e)}",
                "raw":        ""
            }

    def _parse(self, text):
        """Parse Groq response — identical output format to original Gemini parser."""
        result = {
            "verdict":    "UNKNOWN",
            "confidence": 0,
            "key_level":  "N/A",
            "warning":    "N/A",
            "summary":    text[:300],
            "raw":        text
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
                except Exception:
                    pass
            elif line.startswith("KEY_LEVEL:"):
                result["key_level"] = line.replace("KEY_LEVEL:", "").strip()
            elif line.startswith("WARNING:"):
                result["warning"] = line.replace("WARNING:", "").strip()
            elif line.startswith("SUMMARY:"):
                result["summary"] = line.replace("SUMMARY:", "").strip()
        return result