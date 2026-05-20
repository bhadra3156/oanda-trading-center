"""Supabase Database Client"""
import os, json, logging, urllib.request
from datetime import datetime
logger = logging.getLogger(__name__)

class SupabaseClient:
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL","").rstrip("/")
        self.key = os.getenv("SUPABASE_KEY","")

    def _request(self, method, table, data=None, params=""):
        if not self.url or not self.key:
            return None
        url = f"{self.url}/rest/v1/{table}{params}"
        headers = {
            "apikey":        self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal"
        }
        body = json.dumps(data).encode() if data else None
        req  = urllib.request.Request(url, data=body,
                                      headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = r.read()
                return json.loads(resp) if resp else {}
        except Exception as e:
            logger.error(f"Supabase error: {e}")
            return None

    def save_signal(self, instrument, sig):
        ai = sig.get("ai") or {}
        data = {
            "instrument":  instrument,
            "signal":      sig.get("signal"),
            "confidence":  sig.get("confidence"),
            "rsi":         sig.get("rsi"),
            "ema200":      sig.get("ema200"),
            "atr":         sig.get("atr"),
            "trend":       sig.get("trend"),
            "session":     sig.get("session"),
            "entry":       sig.get("entry"),
            "sl":          sig.get("sl"),
            "tp":          sig.get("tp"),
            "ai_verdict":  ai.get("verdict"),
            "ai_summary":  ai.get("summary"),
        }
        return self._request("POST", "signals", data)

    def save_trade(self, trade):
        return self._request("POST", "trades", trade)

    def save_account_snapshot(self, summary):
        data = {
            "balance":          float(summary.get("balance",0)),
            "nav":              float(summary.get("NAV",0)),
            "unrealized_pl":    float(summary.get("unrealizedPL",0)),
            "margin_used":      float(summary.get("marginUsed",0)),
            "margin_available": float(summary.get("marginAvailable",0)),
            "open_trades":      int(summary.get("openTradeCount",0)),
        }
        return self._request("POST", "account_snapshots", data)

    def get_journal_stats(self):
        trades = self._request("GET", "trades", params="?order=created_at.desc&limit=500") or []
        closed = [t for t in trades if t.get("outcome")]
        wins   = [t for t in closed if t.get("outcome")=="WIN"]
        losses = [t for t in closed if t.get("outcome")=="LOSS"]
        total_pnl = sum(float(t.get("pnl",0)) for t in closed)
        by_inst = {}
        for t in closed:
            inst = t.get("instrument","")
            if inst not in by_inst:
                by_inst[inst] = {"wins":0,"losses":0,"pnl":0.0}
            if t.get("outcome")=="WIN": by_inst[inst]["wins"]+=1
            else: by_inst[inst]["losses"]+=1
            by_inst[inst]["pnl"]+=float(t.get("pnl",0))
        return {
            "total_trades":  len(trades),
            "closed_trades": len(closed),
            "open_trades":   len(trades)-len(closed),
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins)/len(closed)*100,1) if closed else 0,
            "total_pnl":     round(total_pnl,2),
            "by_instrument": by_inst,
            "recent":        trades[:20],
        }
