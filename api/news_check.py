"""
news_check.py
News Blackout Check — blocks trades 30 mins before/after high-impact events.
Uses ForexFactory calendar (free, no API key needed).
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# How many minutes before/after a high-impact event to block trading
BLACKOUT_MINUTES = 30

# Instrument → affected currencies/keywords
INSTRUMENT_CURRENCIES = {
    "EUR_USD":    ["USD", "EUR"],
    "GBP_JPY":    ["GBP", "JPY"],
    "XAU_USD":    ["USD", "Gold", "NFP", "FOMC", "CPI", "Fed"],
    "XAG_USD":    ["USD", "Silver", "NFP", "FOMC", "CPI"],
    "XPD_USD":    ["USD", "NFP", "FOMC"],
    "NATGAS_USD": ["USD", "NFP", "EIA", "Gas"],
    "WTICO_USD":  ["USD", "Oil", "EIA", "OPEC", "NFP"],
    "CORN_USD":   ["USD", "USDA", "NFP"],
    "SUGAR_USD":  ["USD", "NFP"],
    "WHEAT_USD":  ["USD", "USDA", "NFP"],
    "SOYBN_USD":  ["USD", "USDA", "NFP"],
    "SPX500_USD": ["USD", "NFP", "FOMC", "CPI", "Fed", "GDP"],
    "NAS100_USD": ["USD", "NFP", "FOMC", "CPI", "Fed", "GDP"],
    "UK100_GBP":  ["GBP", "BOE", "CPI", "GDP"],
    "DE30_EUR":   ["EUR", "ECB", "CPI", "GDP"],
}

# Keywords that always trigger a blackout (regardless of instrument)
UNIVERSAL_HIGH_IMPACT = [
    "Non-Farm", "NFP", "FOMC", "Federal Reserve", "Fed Rate",
    "Interest Rate", "CPI", "Inflation", "GDP", "Employment",
    "Unemployment", "ECB", "BOE", "BOJ", "RBA", "SNB",
    "Jackson Hole", "Powell", "Lagarde", "Draghi"
]

_news_cache = {"data": [], "fetched_at": None}
_CACHE_TTL_MINUTES = 60


def _fetch_calendar() -> list:
    """Fetch this week's high-impact events from ForexFactory."""
    global _news_cache
    now = datetime.utcnow()

    # Return cache if fresh
    if (_news_cache["fetched_at"] and
            (now - _news_cache["fetched_at"]).seconds < _CACHE_TTL_MINUTES * 60):
        return _news_cache["data"]

    events = []
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
                high = [e for e in data if e.get("impact") == "High"]
                events.extend(high)
        except Exception as e:
            logger.warning(f"Calendar fetch error ({url}): {e}")

    _news_cache = {"data": events, "fetched_at": now}
    return events


def _parse_event_time(event: dict) -> datetime | None:
    """Parse ForexFactory event time to UTC datetime."""
    date_str = event.get("date", "")
    time_str = event.get("time", "")
    if not date_str:
        return None
    try:
        if time_str and time_str != "All Day" and ":" in time_str:
            # ForexFactory times are US Eastern — convert to UTC
            # Approximate: EST = UTC-5, EDT = UTC-4
            # We use UTC-5 as conservative estimate
            dt_str = f"{date_str} {time_str}"
            formats = ["%m-%d-%Y %I:%M%p", "%Y-%m-%d %H:%M", "%m/%d/%Y %I:%M%p"]
            for fmt in formats:
                try:
                    dt = datetime.strptime(dt_str, fmt)
                    # Add 5 hours to convert from EST to UTC
                    return dt + timedelta(hours=5)
                except ValueError:
                    continue
        # Date only — assume 13:30 UTC (common for US releases)
        for fmt in ["%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"]:
            try:
                return datetime.strptime(date_str, fmt).replace(hour=13, minute=30)
            except ValueError:
                continue
    except Exception as e:
        logger.debug(f"Time parse error: {e}")
    return None


def _event_affects_instrument(event: dict, instrument: str) -> bool:
    """Check if a news event affects a specific instrument."""
    title   = event.get("title", "").upper()
    country = event.get("country", "").upper()

    # Universal high-impact keywords affect everything
    for kw in UNIVERSAL_HIGH_IMPACT:
        if kw.upper() in title:
            return True

    # Instrument-specific currency check
    affected = INSTRUMENT_CURRENCIES.get(instrument, ["USD"])
    for currency in affected:
        if currency.upper() in title or currency.upper() in country:
            return True

    return False


def check_news_blackout(instrument: str) -> dict:
    """
    Check if an instrument is currently in a news blackout window.

    Returns:
        {
            "blocked": bool,
            "reason": str,
            "event_title": str,
            "event_time": str,
            "minutes_to_event": int,
            "upcoming_events": list
        }
    """
    now = datetime.utcnow()
    events = _fetch_calendar()

    blocked_by = None
    min_distance = None
    upcoming = []

    for event in events:
        if not _event_affects_instrument(event, instrument):
            continue

        event_time = _parse_event_time(event)
        if not event_time:
            continue

        delta_minutes = (event_time - now).total_seconds() / 60

        # Build upcoming list (next 24 hours)
        if 0 < delta_minutes <= 1440:
            upcoming.append({
                "title":    event.get("title", "Unknown"),
                "country":  event.get("country", ""),
                "time_utc": event_time.strftime("%Y-%m-%d %H:%M UTC"),
                "minutes":  int(delta_minutes),
            })

        # Check blackout window: -BLACKOUT to +BLACKOUT minutes around event
        in_window = -BLACKOUT_MINUTES <= delta_minutes <= BLACKOUT_MINUTES

        if in_window:
            if min_distance is None or abs(delta_minutes) < abs(min_distance):
                min_distance = delta_minutes
                blocked_by   = event

    # Sort upcoming by time
    upcoming.sort(key=lambda x: x["minutes"])

    if blocked_by:
        delta = int(min_distance)
        if delta >= 0:
            timing = f"in {delta} min"
        else:
            timing = f"{abs(delta)} min ago"
        title = blocked_by.get("title", "High-impact news")
        return {
            "blocked":         True,
            "reason":          f"News blackout: {title} {timing}",
            "event_title":     title,
            "event_time":      _parse_event_time(blocked_by).strftime("%H:%M UTC") if _parse_event_time(blocked_by) else "—",
            "minutes_to_event": int(min_distance),
            "upcoming_events": upcoming[:5],
        }

    return {
        "blocked":         False,
        "reason":          "",
        "event_title":     "",
        "event_time":      "",
        "minutes_to_event": None,
        "upcoming_events": upcoming[:5],
    }


def get_all_upcoming_events(hours: int = 24) -> list:
    """Get all high-impact events in the next N hours."""
    now    = datetime.utcnow()
    events = _fetch_calendar()
    result = []
    for event in events:
        et = _parse_event_time(event)
        if not et:
            continue
        delta = (et - now).total_seconds() / 60
        if 0 < delta <= hours * 60:
            result.append({
                "title":    event.get("title", ""),
                "country":  event.get("country", ""),
                "impact":   event.get("impact", ""),
                "time_utc": et.strftime("%Y-%m-%d %H:%M UTC"),
                "minutes":  int(delta),
                "in_blackout": delta <= BLACKOUT_MINUTES,
            })
    result.sort(key=lambda x: x["minutes"])
    return result