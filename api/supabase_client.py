"""
Supabase Database Client
Saves signals, trades, and account snapshots.
Journal queries the signals table (fixed from trades table bug).
"""
import os, json, logging, urllib.request
from datetime import datetime
logger = logging.getLogger(__name__)


class SupabaseClient:
    def __init__(self):
        self.url = os.getenv("SUPABASE_URL", "").rstrip("/")
        self.key = os.getenv("SUPABASE_KEY", "")

    def _request(self, method, table, data=None, params=""):
        if not self.url or not self.key:
            logger.warning("Supabase not configured — skipping DB operation")
            return None
        url     = f"{self.url}/rest/v1/{table}{params}"
        headers = {
            "apikey":        self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type":  "application/json",
            "Prefer":        "return=minimal",
        }
        body = json.dumps(data).encode() if data else None
        req  = urllib.request.Request(
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = r.read()
                return json.loads(resp) if resp else {}
        except Exception as e:
            logger.error(f"Supabase {method} {table} error: {e}")
            return None

    # ── SIGNALS ───────────────────────────────────────────────────────────────
    def save_signal(self, instrument, sig):
        ai = sig.get("ai") or {}
        data = {
            "instrument": instrument,
            "signal":     sig.get("signal"),
            "confidence": sig.get("confidence"),
            "rsi":        sig.get("rsi"),
            "ema200":     sig.get("ema200"),
            "atr":        sig.get("atr"),
            "trend":      sig.get("trend"),
            "session":    sig.get("session"),
            "entry":      sig.get("entry"),
            "sl":         sig.get("sl"),
            "tp":         sig.get("tp"),
            "sl_pips":    sig.get("sl_pips"),
            "tp_pips":    sig.get("tp_pips"),
            "timeframe":  sig.get("timeframe", "H4"),
            "ai_verdict": ai.get("verdict"),
            "ai_summary": ai.get("summary"),
            "ai_warning": ai.get("warning"),
            "ai_provider":ai.get("provider", "groq"),
            "created_at": datetime.utcnow().isoformat(),
        }
        return self._request("POST", "signals", data)

    # ── TRADES ────────────────────────────────────────────────────────────────
    def save_trade(self, trade):
        trade["created_at"] = datetime.utcnow().isoformat()
        return self._request("POST", "trades", trade)

    # ── ACCOUNT SNAPSHOTS ─────────────────────────────────────────────────────
    def save_account_snapshot(self, summary):
        data = {
            "balance":          float(summary.get("balance", 0)),
            "nav":              float(summary.get("NAV", 0)),
            "unrealized_pl":    float(summary.get("unrealizedPL", 0)),
            "margin_used":      float(summary.get("marginUsed", 0)),
            "margin_available": float(summary.get("marginAvailable", 0)),
            "open_trades":      int(summary.get("openTradeCount", 0)),
            "created_at":       datetime.utcnow().isoformat(),
        }
        return self._request("POST", "account_snapshots", data)

    # ── JOURNAL STATS ─────────────────────────────────────────────────────────
    def get_journal_stats(self):
        """
        FIX: Query signals table (not trades table).
        Signals are saved on every BUY/SELL scan.
        Trades are only saved when you actually place an order.
        """
        # Get all signals (BUY/SELL only, ordered by newest first)
        all_signals = self._request(
            "GET", "signals",
            params="?order=created_at.desc&limit=500"
        ) or []

        # Get closed trades for P&L tracking
        closed_trades = self._request(
            "GET", "trades",
            params="?order=created_at.desc&limit=200"
        ) or []

        # Stats from signals
        buy_signals  = [s for s in all_signals if s.get("signal") == "BUY"]
        sell_signals = [s for s in all_signals if s.get("signal") == "SELL"]

        # Stats from closed trades
        closed = [t for t in closed_trades if t.get("outcome")]
        wins   = [t for t in closed if t.get("outcome") == "WIN"]
        losses = [t for t in closed if t.get("outcome") == "LOSS"]
        total_pnl = sum(float(t.get("pnl", 0)) for t in closed)

        # Per-instrument breakdown
        by_inst = {}
        for t in closed:
            inst = t.get("instrument", "")
            if inst not in by_inst:
                by_inst[inst] = {"wins": 0, "losses": 0, "pnl": 0.0}
            if t.get("outcome") == "WIN":
                by_inst[inst]["wins"] += 1
            else:
                by_inst[inst]["losses"] += 1
            by_inst[inst]["pnl"] += float(t.get("pnl", 0))

        # Format for journal page
        # Map signals to journal format
        trades_for_journal = []
        for s in all_signals:
            trades_for_journal.append({
                "Date":       s.get("created_at", ""),
                "Instrument": s.get("instrument", ""),
                "Signal":     s.get("signal", ""),
                "Timeframe":  s.get("timeframe", "H4"),
                "Entry":      s.get("entry"),
                "SL":         s.get("sl"),
                "TP":         s.get("tp"),
                "Confidence": s.get("confidence", 0),
                "Session":    s.get("session", ""),
                "RSI":        s.get("rsi"),
                "Trend":      s.get("trend", ""),
                "ATR":        s.get("atr"),
                "AIVerdict":  s.get("ai_verdict", ""),
                "AIWarning":  s.get("ai_warning", ""),
                "AIProvider": s.get("ai_provider", "groq"),
                "Outcome":    s.get("outcome", ""),
                "PnL":        s.get("pnl", 0),
                "Reasons":    s.get("ai_summary", ""),
            })

        return {
            "total_trades":    len(all_signals),
            "closed_trades":   len(closed),
            "open_trades":     len(all_signals) - len(closed),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate":        round(len(wins) / len(closed) * 100, 1) if closed else 0,
            "total_pnl":       round(total_pnl, 2),
            "by_instrument":   by_inst,
            "trades":          trades_for_journal,
            "recent":          trades_for_journal[:20],
            "buy_signals":     len(buy_signals),
            "sell_signals":    len(sell_signals),
        }