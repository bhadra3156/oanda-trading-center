"""
Read-only MCP connector for the OANDA Trading Machine (Claude custom connector).

Endpoint:  POST /mcp/<MCP_SECRET>   (streamable HTTP, stateless, JSON responses)
The secret in the path is the connector's password: set MCP_SECRET on Render.
Without MCP_SECRET the endpoint is switched off.

Tools are READ-ONLY. No tool here can place, change or close an order.
Also runs the background loop: 4H snapshot, Telegram alerts, keep-awake ping.
"""
import os, json, hmac, asyncio, logging, time, urllib.request
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from api.live import LiveBoard, h4_boundaries, BOARD_INSTRUMENTS
from api.sync import build_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()

MCP_SECRET = os.getenv("MCP_SECRET", "").strip()
SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]

# OANDA price precision per market
DPS = {"XAU_USD": 3, "NATGAS_USD": 3, "WTICO_USD": 3, "CORN_USD": 3, "WHEAT_USD": 3, "SUGAR_USD": 5,
       "SPX500_USD": 1, "UK100_GBP": 1, "GBP_JPY": 3, "GBP_CAD": 5, "USD_JPY": 3, "EUR_USD": 5, "GBP_USD": 5}

_live = None
_oanda = None


def init(oanda):
    global _live, _oanda
    _oanda = oanda
    _live = LiveBoard(oanda, DPS)
    return _live


RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
TOOLS = [
    {"name": "get_board",
     "description": "Live analysis of all 13 markets on the 4H chart from OANDA candles: trend, Ichimoku cloud, 200 EMA, RSI, ATR, PSAR, momentum, divergence, reversal signal, volume pressure, bias score, PDH/PDL, nearest support/resistance, daily/4H/1H trend strip, volatility, retail positioning, currency strength, and a confluence grade. Cached per 4H candle; refresh=true recomputes now (at most once a minute).",
     "inputSchema": {"type": "object", "properties": {"refresh": {"type": "boolean", "description": "Recompute now instead of using this 4H candle's snapshot."}}, "additionalProperties": False},
     "annotations": dict(RO, title="Live market board")},
    {"name": "get_prices",
     "description": "Live bid/ask/spread for the 13 markets, with spread compared to its recent normal.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": dict(RO, title="Live prices")},
    {"name": "get_account_state",
     "description": "OANDA account balance, NAV, margin, open trades (with stop/target) and recent closed trades. Read-only.",
     "inputSchema": {"type": "object", "properties": {"closed": {"type": "integer", "minimum": 1, "maximum": 500, "description": "How many recent closed trades to include (default 100)."}}, "additionalProperties": False},
     "annotations": dict(RO, title="Account and trades")},
]


def _run_tool(name, args):
    if name == "get_board":
        return _live.board(refresh=bool(args.get("refresh")))
    if name == "get_prices":
        data = _live.prices()
        return {"kind": "otm-prices", "at": datetime.now(timezone.utc).isoformat(), "prices": data}
    if name == "get_account_state":
        n = max(1, min(int(args.get("closed") or 100), 500))
        snap = build_snapshot(_oanda.get_account_summary(), _oanda.get_open_trades(), _oanda.get_trade_history(count=n))
        try:
            snap["pnl"] = _oanda.get_daily_pnl()
        except Exception:
            pass
        return snap
    raise KeyError(name)


def _err(id_, code, msg):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}}


async def _handle(msg):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _err(None, -32600, "Invalid request")
    method, id_, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if id_ is None:  # notification
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": want if want in SUPPORTED else SUPPORTED[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "OANDA Trading Center", "version": "1.0.0"},
            "instructions": "Read-only live market data and account state for the OANDA Trading Machine. Cannot place or change orders."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            return _err(id_, -32602, f"Unknown tool: {name}")
        try:
            data = await asyncio.get_running_loop().run_in_executor(None, _run_tool, name, args)
            return {"jsonrpc": "2.0", "id": id_, "result": {
                "content": [{"type": "text", "text": json.dumps(data, separators=(",", ":"))}],
                "structuredContent": data, "isError": False}}
        except Exception as e:
            logger.warning(f"[mcp] {name} failed: {e}")
            return {"jsonrpc": "2.0", "id": id_, "result": {
                "content": [{"type": "text", "text": f"{name} failed: {str(e)[:300]}"}], "isError": True}}
    if method in ("resources/list", "prompts/list"):
        return {"jsonrpc": "2.0", "id": id_, "result": {"resources" if method.startswith("res") else "prompts": []}}
    return _err(id_, -32601, f"Method not found: {method}")


def _authorised(secret):
    return bool(MCP_SECRET) and hmac.compare_digest(secret or "", MCP_SECRET)


@router.post("/mcp/{secret}")
async def mcp_post(secret: str, request: Request):
    if not _authorised(secret) or _live is None:
        return Response(status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err(None, -32700, "Parse error"), status_code=400)
    if isinstance(body, list):
        out = [r for r in [await _handle(m) for m in body] if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    res = await _handle(body)
    return JSONResponse(res) if res is not None else Response(status_code=202)


@router.get("/mcp/{secret}")
async def mcp_get(secret: str):
    # No server-initiated stream: spec says answer 405.
    return Response(status_code=405 if _authorised(secret) else 404)


@router.delete("/mcp/{secret}")
async def mcp_delete(secret: str):
    return Response(status_code=405 if _authorised(secret) else 404)


# ════════════════════════════ background: 4H snapshot, alerts, keep-awake ════════════════════════════
def _pretty(inst):
    return inst.replace("_", "/")


def _ping_self():
    url = os.getenv("BACKEND_URL", "https://oanda-trading-center.onrender.com").rstrip("/") + "/health"
    try:
        urllib.request.urlopen(url, timeout=15).read(64)
    except Exception as e:
        logger.info(f"[live] keep-awake ping failed: {e}")


async def live_loop(telegram):
    """Every 5 min: keep the free Render server awake; after each 4H close: rebuild the board,
    alert new A/B setups on Telegram; nudge open trades at +1R to move the stop to breakeven."""
    loop = asyncio.get_running_loop()
    await asyncio.sleep(45)
    seen_close, last_grades, be_sent = None, {}, set()
    alerts_on = os.getenv("BOARD_ALERTS", "on").lower() != "off"
    while True:
        try:
            await loop.run_in_executor(None, _ping_self)
            if _live is not None:
                last_close, _ = h4_boundaries()
                due = (datetime.now(timezone.utc) - last_close).total_seconds() > 120
                if due and last_close.isoformat() != seen_close:
                    board = await loop.run_in_executor(None, lambda: _live.board(refresh=True, min_age=0))
                    first = seen_close is None
                    seen_close = last_close.isoformat()
                    lines = []
                    for inst, m in board["markets"].items():
                        g = (m or {}).get("grade") or {}
                        key = (g.get("grade"), g.get("dir"))
                        if g.get("grade") in ("A", "B") and last_grades.get(inst) != key and not first:
                            rd = m.get("read", {})
                            lines.append(f"{'🟢' if g['dir'] == 'long' else '🔴'} *{_pretty(inst)}* {g['dir'].upper()} — grade *{g['grade']}* "
                                         f"(score {g['net']:+d}) · price {rd.get('price')} · ATR {rd.get('atr')}")
                        last_grades[inst] = key
                    if lines and alerts_on:
                        telegram.send("📊 *New 4H setups* (Trading Machine rules)\n" + "\n".join(lines) +
                                      "\n\nCheck the chart, then size it in the Trading Machine.")
                # breakeven nudges
                if alerts_on:
                    trades = await loop.run_in_executor(None, _oanda.get_open_trades)
                    px = await loop.run_in_executor(None, _live.prices)
                    for t in trades:
                        try:
                            units = float(t.get("currentUnits")); entry = float(t["price"])
                            sl = float((t.get("stopLossOrder") or {}).get("price"))
                        except (TypeError, ValueError, KeyError):
                            continue
                        side = 1 if units > 0 else -1
                        q = px.get(t.get("instrument")) or {}
                        cur = q.get("bid") if side > 0 else q.get("ask")
                        risk = (entry - sl) * side
                        if not cur or risk <= 0:
                            continue  # stop already at/through breakeven
                        r_now = (cur - entry) * side / risk
                        if r_now >= 1 and t["id"] not in be_sent:
                            be_sent.add(t["id"])
                            telegram.send(f"🛡 *{_pretty(t['instrument'])}* trade #{t['id']} is at *+{r_now:.1f}R*. "
                                          f"Move the stop to breakeven ({entry}) to make it risk-free.")
        except Exception as e:
            logger.warning(f"[live] loop error: {e}")
        await asyncio.sleep(300)
