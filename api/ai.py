"""Google Gemini AI — Free trading analysis"""
import os, json, logging, urllib.request, urllib.error
logger = logging.getLogger(__name__)

class GeminiAnalyst:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                    "gemini-1.5-flash:generateContent?key=")

    def analyse(self, instrument, signal_data):
        if not self.api_key:
            return {"verdict":"UNAVAILABLE","confidence":0,"summary":"Gemini API key not set"}
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
Reasons: {', '.join(signal_data.get('reasons',[]))}

Reply ONLY in this exact format:
VERDICT: [STRONG BUY/BUY/WEAK BUY/WAIT/WEAK SELL/SELL/STRONG SELL]
CONFIDENCE: [0-100]%
KEY_LEVEL: [most important price level]
WARNING: [biggest risk for this trade]
SUMMARY: [2 sentences max in plain English]"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature":0.2,"maxOutputTokens":250}
        }
        try:
            req = urllib.request.Request(
                self.url + self.api_key,
                data=json.dumps(payload).encode(),
                headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                result = json.loads(r.read())
                text = result["candidates"][0]["content"]["parts"][0]["text"]
                return self._parse(text)
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return {"verdict":"UNAVAILABLE","confidence":0,
                    "summary":f"AI unavailable: {str(e)}"}

    def _parse(self, text):
        result = {"verdict":"UNKNOWN","confidence":0,
                  "key_level":"N/A","warning":"N/A",
                  "summary":text[:300],"raw":text}
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("VERDICT:"):
                result["verdict"] = line.replace("VERDICT:","").strip()
            elif line.startswith("CONFIDENCE:"):
                try: result["confidence"]=int(line.replace("CONFIDENCE:","").replace("%","").strip())
                except: pass
            elif line.startswith("KEY_LEVEL:"):
                result["key_level"] = line.replace("KEY_LEVEL:","").strip()
            elif line.startswith("WARNING:"):
                result["warning"] = line.replace("WARNING:","").strip()
            elif line.startswith("SUMMARY:"):
                result["summary"] = line.replace("SUMMARY:","").strip()
        return result
