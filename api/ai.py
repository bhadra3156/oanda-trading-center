"""
ai.py — GROQ Primary + Claude Haiku Fallback + 5-minute caching
Never shows broken state — always returns a result.
"""
import os, time, logging
logger = logging.getLogger(__name__)

# ── Cache: instrument -> {result, timestamp} ──────────────────────────────────
_cache: dict = {}
CACHE_TTL = 300  # 5 minutes

def _cached(instrument: str):
    entry = _cache.get(instrument)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        result = dict(entry["result"])
        result["cached"] = True
        result["cache_age"] = int(time.time() - entry["ts"])
        return result
    return None

def _store(instrument: str, result: dict):
    _cache[instrument] = {"result": result, "ts": time.time()}

# ── GROQ client ───────────────────────────────────────────────────────────────
_groq_client = None

def _get_groq():
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
            key = os.getenv("GROQ_API_KEY")
            if key:
                _groq_client = Groq(api_key=key)
        except Exception as e:
            logger.error(f"GROQ init error: {e}")
    return _groq_client

# ── Claude via direct HTTP (no anthropic SDK needed) ─────────────────────────
def _call_claude(prompt: str) -> str:
    import urllib.request, json
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not set")
    payload = json.dumps({
        "model":      "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "messages":   [{"role": "user", "content": prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key":         key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data["content"][0]["text"]

# ── Shared prompt builder ─────────────────────────────────────────────────────
def _build_prompt(instrument: str, signal_data: dict) -> str:
    return f"""You are a professional forex and commodities trader analysing an H4 signal.

Instrument: {instrument.replace('_', '/')}
Signal:     {signal_data.get('signal')}
Price:      {signal_data.get('price')}
RSI(14):    {signal_data.get('rsi')}
Trend:      {signal_data.get('trend')}
EMA200:     {signal_data.get('ema200')}
ATR:        {signal_data.get('atr')}
Session:    {signal_data.get('session')}
Entry:      {signal_data.get('entry')}
Stop Loss:  {signal_data.get('sl')}
Take Profit:{signal_data.get('tp')}
Reasons:    {', '.join(signal_data.get('reasons', []))}

Reply ONLY in this exact format — no extra text:
VERDICT: [STRONG BUY/BUY/WEAK BUY/WAIT/WEAK SELL/SELL/STRONG SELL]
CONFIDENCE: [0-100]%
KEY_LEVEL: [most important price level to watch]
WARNING: [biggest risk for this trade in one sentence]
SUMMARY: [2 sentences max — plain English, specific to this instrument]"""

# ── Response parser ───────────────────────────────────────────────────────────
def _parse(text: str) -> dict:
    result = {
        "verdict":    "UNKNOWN",
        "confidence": 0,
        "key_level":  "N/A",
        "warning":    "N/A",
        "summary":    text[:300] if text else "",
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

def _unavailable(reason: str = "") -> dict:
    return {
        "verdict":    "UNAVAILABLE",
        "confidence": 0,
        "key_level":  "N/A",
        "warning":    "N/A",
        "summary":    f"AI unavailable: {reason}" if reason else "AI unavailable",
        "raw":        "",
        "cached":     False,
        "provider":   "none",
    }

# ── Main analyst class ────────────────────────────────────────────────────────
class GeminiAnalyst:
    """
    GROQ primary (free, fast).
    Claude Haiku fallback (cheap — ~$0.001 per 1000 signals).
    5-minute cache — never hammers the API on every scan.
    """

    def analyse(self, instrument: str, signal_data: dict) -> dict:
        # 1. Return cached result if fresh
        cached = _cached(instrument)
        if cached:
            return cached

        prompt = _build_prompt(instrument, signal_data)

        # 2. Try GROQ first
        groq = _get_groq()
        if groq:
            try:
                completion = groq.chat.completions.create(
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
                text   = completion.choices[0].message.content or ""
                result = _parse(text)
                result["cached"]   = False
                result["provider"] = "groq"
                _store(instrument, result)
                logger.info(f"GROQ analysis OK: {instrument}")
                return result

            except Exception as e:
                err = str(e)
                if "429" in err or "rate_limit" in err or "tokens per day" in err:
                    logger.warning(f"GROQ rate limited for {instrument} — trying Claude")
                else:
                    logger.error(f"GROQ error for {instrument}: {err[:150]}")

        # 3. Fallback to Claude Haiku
        try:
            text   = _call_claude(prompt)
            result = _parse(text)
            result["cached"]   = False
            result["provider"] = "claude"
            _store(instrument, result)
            logger.info(f"Claude fallback OK: {instrument}")
            return result

        except Exception as e:
            logger.error(f"Claude fallback error for {instrument}: {str(e)[:150]}")

        # 4. Last resort — return cached even if stale
        stale = _cache.get(instrument)
        if stale:
            result = dict(stale["result"])
            result["cached"]     = True
            result["stale"]      = True
            result["cache_age"]  = int(time.time() - stale["ts"])
            result["summary"]   += f" [Cached {result['cache_age']//60}min ago]"
            logger.warning(f"Serving stale cache for {instrument}")
            return result

        return _unavailable("Both GROQ and Claude unavailable")

    def analyse_debrief(self, prompt: str) -> str:
        # Try GROQ first for debrief
        groq = _get_groq()
        if groq:
            try:
                completion = groq.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are an expert trading performance coach. "
                                "Analyse the trader's history honestly. "
                                "Be direct, specific and actionable. "
                                "Format with numbered sections."
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
                if "429" not in err and "rate_limit" not in err:
                    logger.error(f"GROQ debrief error: {err[:150]}")

        # Fallback to Claude for debrief
        try:
            return _call_claude(prompt)
        except Exception as e:
            return f"AI debrief unavailable: {str(e)[:200]}"