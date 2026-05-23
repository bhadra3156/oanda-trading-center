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
    # ── PERFORMANCE STATS ────────────────────────────────────────────────────
    def get_performance_stats(self):
        """Equity curve + performance metrics from account snapshots."""
        snapshots = self._request(
            "GET", "account_snapshots",
            params="?order=created_at.asc&limit=500"
        ) or []

        signals = self._request(
            "GET", "signals",
            params="?order=created_at.asc&limit=1000"
        ) or []

        trades = self._request(
            "GET", "trades",
            params="?order=created_at.desc&limit=200"
        ) or []

        # Equity curve from snapshots
        equity_curve = []
        peak_balance = 0.0
        for s in snapshots:
            bal = float(s.get("balance", 0))
            nav = float(s.get("nav", bal))
            if nav > peak_balance:
                peak_balance = nav
            equity_curve.append({
                "date":    s.get("created_at", "")[:10],
                "balance": round(bal, 2),
                "nav":     round(nav, 2),
            })

        # Current drawdown from peak
        current_nav    = equity_curve[-1]["nav"] if equity_curve else 0
        drawdown_pct   = round((peak_balance - current_nav) / peak_balance * 100, 2) if peak_balance > 0 else 0
        drawdown_abs   = round(peak_balance - current_nav, 2)

        # Monthly returns
        monthly = {}
        for s in snapshots:
            month = s.get("created_at", "")[:7]
            if month not in monthly:
                monthly[month] = {"start": float(s.get("balance", 0)), "end": float(s.get("balance", 0))}
            monthly[month]["end"] = float(s.get("balance", 0))

        monthly_returns = []
        for month, d in sorted(monthly.items()):
            if d["start"] > 0:
                ret = round((d["end"] - d["start"]) / d["start"] * 100, 2)
                monthly_returns.append({"month": month, "return_pct": ret,
                                        "start": d["start"], "end": d["end"]})

        # Starting balance (first snapshot or fallback)
        starting_balance = equity_curve[0]["balance"] if equity_curve else 627.0
        target_balance   = 4000.0

        # Progress to target
        progress_pct = round((current_nav - starting_balance) / (target_balance - starting_balance) * 100, 1) if target_balance > starting_balance else 0
        progress_pct = max(0, min(100, progress_pct))

        # Project target date based on avg monthly return
        avg_monthly_ret = 0.0
        if monthly_returns:
            avg_monthly_ret = sum(m["return_pct"] for m in monthly_returns) / len(monthly_returns)

        projected_months = None
        if avg_monthly_ret > 0 and current_nav < target_balance:
            import math
            projected_months = math.ceil(math.log(target_balance / current_nav) / math.log(1 + avg_monthly_ret / 100))

        # Signal accuracy per instrument
        inst_accuracy = {}
        for s in signals:
            inst = s.get("instrument", "")
            if inst not in inst_accuracy:
                inst_accuracy[inst] = {"signals": 0, "high_conf": 0, "avg_conf": []}
            inst_accuracy[inst]["signals"] += 1
            conf = float(s.get("confidence", 0))
            inst_accuracy[inst]["avg_conf"].append(conf)
            if conf >= 65:
                inst_accuracy[inst]["high_conf"] += 1

        for inst in inst_accuracy:
            confs = inst_accuracy[inst]["avg_conf"]
            inst_accuracy[inst]["avg_confidence"] = round(sum(confs)/len(confs), 1) if confs else 0
            del inst_accuracy[inst]["avg_conf"]

        # Consistency score (0-100)
        # Based on: % signals above 65% confidence, session distribution
        total_sigs    = len(signals)
        high_conf_sigs= sum(1 for s in signals if float(s.get("confidence",0)) >= 65)
        consistency   = round(high_conf_sigs / total_sigs * 100, 1) if total_sigs > 0 else 0

        # Sharpe ratio (simplified — using closed trades)
        closed = [t for t in trades if t.get("pnl") is not None]
        sharpe = None
        if len(closed) >= 5:
            pnls = [float(t.get("pnl", 0)) for t in closed]
            avg  = sum(pnls) / len(pnls)
            variance = sum((p - avg)**2 for p in pnls) / len(pnls)
            std  = variance ** 0.5
            sharpe = round(avg / std, 2) if std > 0 else None

        return {
            "equity_curve":       equity_curve,
            "peak_balance":       round(peak_balance, 2),
            "current_nav":        round(current_nav, 2),
            "starting_balance":   round(starting_balance, 2),
            "drawdown_pct":       drawdown_pct,
            "drawdown_abs":       drawdown_abs,
            "monthly_returns":    monthly_returns,
            "avg_monthly_return": round(avg_monthly_ret, 2),
            "target_balance":     target_balance,
            "progress_pct":       progress_pct,
            "projected_months":   projected_months,
            "inst_accuracy":      inst_accuracy,
            "consistency_score":  consistency,
            "sharpe_ratio":       sharpe,
            "total_signals":      total_sigs,
            "snapshots_count":    len(snapshots),
        }

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