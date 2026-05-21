"""
Correlated Positions Checker
Drop this file into your /api/ folder as correlation.py
Then import and call check_correlation() from signals.py and main.py
"""

# ── Correlation map ──────────────────────────────────────────────────────────
# Each group shares strong positive correlation.
# Instruments in the same group move together on macro events.

CORRELATION_GROUPS = {
    "AGRI_GRAINS": {
        "instruments": ["CORN_USD", "WHEAT_USD", "SOYBN_USD"],
        "label": "Grains",
        "reason": "All move together on weather events, USDA reports, and USD strength",
        "strength": "STRONG",   # 0.75–0.95 typical
    },
    "AGRI_SOFT": {
        "instruments": ["SUGAR_USD", "CORN_USD", "WHEAT_USD", "SOYBN_USD"],
        "label": "Agricultural commodities",
        "reason": "Broad commodity risk-on/risk-off moves, USD index, energy costs",
        "strength": "MODERATE",  # 0.55–0.75 typical
    },
    "ENERGY": {
        "instruments": ["WTICO_USD", "BCO_USD", "NATGAS_USD"],
        "label": "Energy",
        "reason": "OPEC decisions, US inventory data, and geopolitical risk affect all together",
        "strength": "MODERATE",
    },
    "METALS_PRECIOUS": {
        "instruments": ["XAU_USD", "XAG_USD", "XPD_USD"],
        "label": "Precious metals",
        "reason": "All driven by real yields, USD, and safe-haven flows",
        "strength": "STRONG",
    },
    "FX_USD_MAJORS": {
        "instruments": ["EUR_USD", "GBP_USD", "AUD_USD", "NZD_USD"],
        "label": "USD majors",
        "reason": "All inversely correlated to USD index (DXY). Fed decisions move all simultaneously",
        "strength": "STRONG",
    },
    "FX_JPY_CROSS": {
        "instruments": ["GBP_JPY", "EUR_JPY", "USD_JPY"],
        "label": "JPY crosses",
        "reason": "BOJ policy and risk sentiment move all JPY pairs together",
        "strength": "STRONG",
    },
    "INDICES_US": {
        "instruments": ["SPX500_USD", "NAS100_USD", "US30_USD"],
        "label": "US indices",
        "reason": "Risk sentiment, Fed policy, and earnings season drive all together",
        "strength": "VERY_STRONG",  # 0.90+ typical
    },
    "INDICES_EU": {
        "instruments": ["UK100_GBP", "DE30_EUR"],
        "label": "European indices",
        "reason": "ECB policy and European risk sentiment",
        "strength": "STRONG",
    },
}

# Inverse correlations (one goes up when the other goes down)
INVERSE_CORRELATIONS = [
    {
        "a": "XAU_USD",
        "b": "USD_JPY",
        "reason": "Gold and USDJPY are both safe-haven proxies but move in opposite directions",
    },
    {
        "a": "WTICO_USD",
        "b": "USD_CAD",
        "reason": "CAD is a petrocurrency — oil up means CAD strengthens (USD/CAD falls)",
    },
]

STRENGTH_COLOUR = {
    "VERY_STRONG": "danger",
    "STRONG":      "warning",
    "MODERATE":    "info",
}


def _normalise(instrument: str) -> str:
    """Normalise instrument names: EUR/USD → EUR_USD, NATGAS → NATGAS_USD, etc."""
    inst = instrument.replace("/", "_").replace("-", "_").upper().strip()
    # Handle Oanda shorthand that lacks a quote currency
    shorthands = {
        "NATGAS":  "NATGAS_USD",
        "WTICO":   "WTICO_USD",
        "BCO":     "BCO_USD",
        "XAU":     "XAU_USD",
        "XAG":     "XAG_USD",
        "XPD":     "XPD_USD",
        "CORN":    "CORN_USD",
        "WHEAT":   "WHEAT_USD",
        "SOYBN":   "SOYBN_USD",
        "SUGAR":   "SUGAR_USD",
        "SPX500":  "SPX500_USD",
        "NAS100":  "NAS100_USD",
        "US30":    "US30_USD",
        "UK100":   "UK100_GBP",
        "DE30":    "DE30_EUR",
    }
    return shorthands.get(inst, inst)


def check_correlation(new_instrument: str, new_direction: str, open_positions: list) -> dict:
    """
    Check whether a proposed trade conflicts with existing open positions.

    Parameters
    ----------
    new_instrument  : str   e.g. "CORN_USD" or "CORN/USD"
    new_direction   : str   "BUY" or "SELL"
    open_positions  : list  Each item must have keys:
                            - instrument (str)
                            - direction  ("BUY"/"SELL"/"LONG"/"SHORT")

    Returns
    -------
    dict with keys:
        safe        : bool   True = no correlated conflict found
        warnings    : list   List of warning dicts (may be empty)
        block_trade : bool   True = at least one VERY_STRONG same-direction conflict
    """
    new_inst = _normalise(new_instrument)
    new_dir  = "BUY" if new_direction.upper() in ("BUY", "LONG") else "SELL"

    warnings    = []
    block_trade = False

    for pos in open_positions:
        open_inst = _normalise(pos.get("instrument", ""))
        open_dir  = "BUY" if str(pos.get("direction", "")).upper() in ("BUY", "LONG") else "SELL"

        if open_inst == new_inst:
            continue  # same instrument — already has position, that's an Oanda concern

        # ── Check positive correlations ───────────────────────────────────────
        for group_key, group in CORRELATION_GROUPS.items():
            members = group["instruments"]
            if new_inst in members and open_inst in members:
                if new_dir == open_dir:
                    # Same direction in correlated pair → doubles the risk
                    severity = group["strength"]
                    warning = {
                        "type":          "CORRELATED_SAME_DIRECTION",
                        "severity":      severity,
                        "colour":        STRENGTH_COLOUR.get(severity, "info"),
                        "group_label":   group["label"],
                        "open_instrument": open_inst,
                        "open_direction":  open_dir,
                        "new_instrument":  new_inst,
                        "new_direction":   new_dir,
                        "reason":          group["reason"],
                        "message": (
                            f"You already have {open_inst} {open_dir} open. "
                            f"{new_inst} and {open_inst} are {severity.lower().replace('_',' ')}ly correlated "
                            f"({group['label']}). A single macro event could hit both positions simultaneously."
                        ),
                    }
                    if severity == "VERY_STRONG":
                        block_trade = True
                    warnings.append(warning)
                else:
                    # Opposite direction in correlated pair → positions hedge each other (wasted margin)
                    warnings.append({
                        "type":          "CORRELATED_OPPOSITE_DIRECTION",
                        "severity":      "INFO",
                        "colour":        "info",
                        "group_label":   group["label"],
                        "open_instrument": open_inst,
                        "open_direction":  open_dir,
                        "new_instrument":  new_inst,
                        "new_direction":   new_dir,
                        "reason":          group["reason"],
                        "message": (
                            f"{new_inst} {new_dir} and {open_inst} {open_dir} are in the same correlated group "
                            f"but opposite directions. These positions partially hedge each other — "
                            f"you may be wasting margin with low net exposure."
                        ),
                    })

        # ── Check inverse correlations ────────────────────────────────────────
        for inv in INVERSE_CORRELATIONS:
            pair = {inv["a"], inv["b"]}
            if {new_inst, open_inst} == pair:
                # Inverse correlation: same direction = hedge (good), opposite = doubled risk
                # e.g. buying gold AND buying USDJPY = both are "risk off" plays → correlated same direction
                if new_dir == open_dir:
                    warnings.append({
                        "type":          "INVERSE_CORRELATED_SAME_DIRECTION",
                        "severity":      "MODERATE",
                        "colour":        "warning",
                        "open_instrument": open_inst,
                        "open_direction":  open_dir,
                        "new_instrument":  new_inst,
                        "new_direction":   new_dir,
                        "reason":          inv["reason"],
                        "message": (
                            f"{new_inst} and {open_inst} are inversely correlated. "
                            f"Taking the same direction on both may not provide the hedge you expect. "
                            f"{inv['reason']}"
                        ),
                    })

    return {
        "safe":        len(warnings) == 0,
        "warnings":    warnings,
        "block_trade": block_trade,
        "summary":     _build_summary(warnings, block_trade),
    }


def _build_summary(warnings: list, block_trade: bool) -> str:
    if not warnings:
        return "No correlation conflicts detected."
    danger  = [w for w in warnings if w["severity"] in ("VERY_STRONG", "STRONG")]
    caution = [w for w in warnings if w["severity"] in ("MODERATE",)]
    info    = [w for w in warnings if w["severity"] == "INFO"]
    parts = []
    if block_trade:
        parts.append("BLOCKED: Very strong correlation conflict.")
    if danger:
        parts.append(f"{len(danger)} strong correlation warning(s).")
    if caution:
        parts.append(f"{len(caution)} moderate correlation caution(s).")
    if info:
        parts.append(f"{len(info)} informational note(s).")
    return " ".join(parts)


def get_correlation_map_for_instrument(instrument: str) -> list:
    """
    Return all instruments correlated with the given instrument.
    Used to display the correlation map in the frontend.
    """
    inst = _normalise(instrument)
    related = []
    for group_key, group in CORRELATION_GROUPS.items():
        if inst in group["instruments"]:
            others = [i for i in group["instruments"] if i != inst]
            related.append({
                "group":     group["label"],
                "strength":  group["strength"],
                "colour":    STRENGTH_COLOUR.get(group["strength"], "info"),
                "reason":    group["reason"],
                "correlated_with": others,
            })
    return related