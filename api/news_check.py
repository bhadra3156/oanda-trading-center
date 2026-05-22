"""
news_check.py — Economic calendar news blackout checker
Uses FMP API (free tier) or falls back to hardcoded schedule
"""

import os
import logging
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# Instruments mapped to currencies for news filtering
INSTRUMENT_CURRENCIES = {
    "EUR_USD":   ["EUR", "USD"],
    "GBP_JPY":   ["GBP", "JPY"],
    "XAU_USD":   ["USD"],
    "XAG_USD":   ["USD"],
    "XPD_USD":   ["USD"],
    "NATGAS_USD":["USD"],
    "WTICO_USD": ["USD"],
    "CORN_USD":  ["USD"],
    "SUGAR_USD": ["USD"],
    "WHEAT_USD": ["USD"],
    "SOYBN_USD": ["USD"],
    "SPX500_USD":["USD"],
    "NAS100_USD":["USD"],
    "UK100_GBP": ["GBP"],
    "DE30_EUR":  ["EUR"],
}

# Fallback high-impact news schedule (UTC times)
HARDCODED_EVENTS = [
    {"title": "US CPI",              "country": "USD", "impact": "High", "hour": 13, "minute": 30, "weekday": None},
    {"title": "US NFP",              "country": "USD", "impact": "High", "hour": 13, "minute": 30, "weekday": 4},
    {"title": "FOMC Rate Decision",  "country": "USD", "impact": "High", "hour": 19, "minute": 0,  "weekday": None},
    {"title": "ECB Rate Decision",   "country": "EUR", "impact": "High", "hour": 13, "minute": 15, "weekday": None},
    {"title": "BOE Rate Decision",   "country": "GBP", "impact": "High", "hour": 12, "minute": 0,  "weekday": None},
    {"title": "US GDP",              "country": "USD", "impact": "High", "hour": 13, "minute": 30, "weekday": None},
    {"title": "EIA Oil Inventories", "country": "USD", "impact": "High", "hour": 15, "minute": 30, "weekday": 2},
    {"title": "US Retail Sales",     "country": "USD", "impact": "High", "hour": 13, "minute": 30, "weekday": None},
    {"title": "US PPI",              "country": "USD", "impact": "High", "hour": 13, "minute": 30, "weekday": None},
    {"title": "UK CPI",              "country": "GBP", "impact": "High", "hour": 7,  "minute": 0,  "weekday": None},
    {"title": "EU CPI",              "country": "EUR", "impact": "High", "hour": 10, "minute": 0,  "weekday": None},
    {"title": "Japan BOJ Decision",  "country": "JPY", "impact": "High", "hour": 3,  "minute": 0,  "weekday": None},
    {"title": "US JOLTS",            "country": "USD", "impact": "High", "hour": 14, "minute": 0,  "weekday": None},
    {"title": "ADP Employment",      "country": "USD", "impact": "High", "hour": 13, "minute": 15, "weekday": 2},
]

BLACKOUT_MINUTES = 30


def get_all_upcoming_events(hours: int = 24) -> list:
    """
    Returns upcoming high-impact events in the next N hours.
    Tries FMP API first, falls back to hardcoded schedule.
    """
    if FMP_API_KEY:
        try:
            return _fetch_fmp_events(hours)
        except Exception as e:
            logger.warning(f"FMP calendar failed, using fallback: {e}")

    return _get_hardcoded_events(hours)


def check_news_blackout(instrument: str) -> dict:
    """
    Check if an instrument is in a news blackout window.
    Returns dict with blackout status and relevant events.
    """
    currencies = INSTRUMENT_CURRENCIES.get(instrument, ["USD"])
    now = datetime.utcnow()
    events = get_all_upcoming_events(hours=1)

    active_blackouts = []
    upcoming_warnings = []

    for event in events:
        country = event.get("country", "")
        if country not in currencies:
            continue

        # Parse event time
        event_time_str = event.get("time_utc") or event.get("date") or ""
        try:
            if "T" in event_time_str:
                event_time = datetime.fromisoformat(event_time_str.replace("Z", ""))
            else:
                # Try "YYYY-MM-DD HH:MM UTC" format
                clean = event_time_str.replace(" UTC", "").strip()
                event_time = datetime.strptime(clean, "%Y-%m-%d %H:%M")
        except Exception:
            continue

        minutes_away = (event_time - now).total_seconds() / 60

        if -BLACKOUT_MINUTES <= minutes_away <= BLACKOUT_MINUTES:
            active_blackouts.append({
                "title":       event.get("title"),
                "country":     country,
                "minutes_away": round(minutes_away, 1),
                "time_utc":    event_time_str,
            })
        elif 0 < minutes_away <= 60:
            upcoming_warnings.append({
                "title":       event.get("title"),
                "country":     country,
                "minutes_away": round(minutes_away, 1),
                "time_utc":    event_time_str,
            })

    in_blackout = len(active_blackouts) > 0

    return {
        "instrument":        instrument,
        "in_blackout":       in_blackout,
        "active_blackouts":  active_blackouts,
        "upcoming_warnings": upcoming_warnings,
        "message": (
            f"BLACKOUT: {active_blackouts[0]['title']} in {active_blackouts[0]['minutes_away']:.0f} min"
            if in_blackout else "Clear to trade"
        ),
    }


def _fetch_fmp_events(hours: int) -> list:
    """Fetch events from Financial Modeling Prep API."""
    now   = datetime.utcnow()
    end   = now + timedelta(hours=hours)
    url   = "https://financialmodelingprep.com/api/v3/economic_calendar"
    params = {
        "from":    now.strftime("%Y-%m-%d"),
        "to":      end.strftime("%Y-%m-%d"),
        "apikey":  FMP_API_KEY,
    }
    resp = requests.get(url, params=params, timeout=8)
    resp.raise_for_status()
    data = resp.json()

    events = []
    for item in data:
        if item.get("impact") not in ("High", "Medium"):
            continue
        events.append({
            "title":      item.get("event", "Unknown"),
            "country":    item.get("country", ""),
            "impact":     item.get("impact", ""),
            "date":       item.get("date", ""),
            "time_utc":   item.get("date", ""),
            "source":     "fmp",
        })
    return events


def _get_hardcoded_events(hours: int) -> list:
    """Return hardcoded events that fall within the next N hours."""
    now    = datetime.utcnow()
    cutoff = now + timedelta(hours=hours)
    events = []

    for ev in HARDCODED_EVENTS:
        # Build a datetime for today
        candidate = now.replace(
            hour=ev["hour"], minute=ev["minute"], second=0, microsecond=0
        )
        # Also check tomorrow
        for dt in [candidate, candidate + timedelta(days=1)]:
            if ev["weekday"] is not None and dt.weekday() != ev["weekday"]:
                continue
            if now <= dt <= cutoff:
                events.append({
                    "title":      ev["title"],
                    "country":    ev["country"],
                    "impact":     ev["impact"],
                    "date":       dt.strftime("%Y-%m-%d %H:%M UTC"),
                    "time_utc":   dt.strftime("%Y-%m-%d %H:%M UTC"),
                    "in_blackout": abs((dt - now).total_seconds()) <= BLACKOUT_MINUTES * 60,
                    "source":     "hardcoded",
                })

    return events