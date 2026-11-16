"""
Build the snapshot the "Send to Trading Machine" button copies.
Pure functions only, so they can be tested without an OANDA connection.
Read-only: nothing here places, changes or closes an order.
"""
from datetime import datetime, timezone


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _order_price(t, key):
    o = t.get(key) or {}
    return _f(o.get("price"))


def map_open(t):
    units = _f(t.get("currentUnits")) or 0.0
    return {
        "id": str(t.get("id")),
        "instrument": t.get("instrument"),
        "side": "long" if units > 0 else "short",
        "units": abs(units),
        "initialUnits": abs(_f(t.get("initialUnits")) or 0.0),
        "entry": _f(t.get("price")),
        "openTime": t.get("openTime"),
        "stop": _order_price(t, "stopLossOrder"),
        "trailing": _f((t.get("trailingStopLossOrder") or {}).get("distance")),
        "tp": _order_price(t, "takeProfitOrder"),
        "realizedPL": _f(t.get("realizedPL")) or 0.0,
        "unrealizedPL": _f(t.get("unrealizedPL")),
        "financing": _f(t.get("financing")) or 0.0,
    }


def map_closed(t):
    units = _f(t.get("initialUnits")) or 0.0
    return {
        "id": str(t.get("id")),
        "instrument": t.get("instrument"),
        "side": "long" if units > 0 else "short",
        "units": abs(units),
        "entry": _f(t.get("price")),
        "exit": _f(t.get("averageClosePrice")),
        "openTime": t.get("openTime"),
        "closeTime": t.get("closeTime"),
        "stop": _order_price(t, "stopLossOrder"),
        "tp": _order_price(t, "takeProfitOrder"),
        "realizedPL": _f(t.get("realizedPL")) or 0.0,
        "financing": _f(t.get("financing")) or 0.0,
    }


def map_account(a):
    return {
        "currency": a.get("currency"),
        "balance": _f(a.get("balance")),
        "nav": _f(a.get("NAV")),
        "unrealizedPL": _f(a.get("unrealizedPL")),
        "marginUsed": _f(a.get("marginUsed")),
        "marginAvailable": _f(a.get("marginAvailable")),
        "openTradeCount": int(_f(a.get("openTradeCount")) or 0),
        "pl": _f(a.get("pl")),
    }


def build_snapshot(account, open_trades, closed_trades, prices=None):
    closed = [map_closed(t) for t in (closed_trades or []) if t.get("state", "CLOSED") == "CLOSED"]
    closed.sort(key=lambda x: x.get("closeTime") or "", reverse=True)
    return {
        "kind": "otm-sync",
        "v": 1,
        "at": datetime.now(timezone.utc).isoformat(),
        "account": map_account(account or {}),
        "open": [map_open(t) for t in (open_trades or [])],
        "closed": closed,
        "prices": prices or {},
    }
