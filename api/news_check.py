"""
news_check.py  —  News Blackout System v3
Tries multiple free sources. Falls back to a smart weekly schedule.
Never returns empty — always protects the trader.
"""

import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BLACKOUT_MINUTES  = 30
CACHE_TTL_MINUTES = 180

INSTRUMENT_CURRENCIES = {
    "EUR_USD":    ["USD","EUR"],
    "GBP_JPY":    ["GBP","JPY"],
    "XAU_USD":    ["USD","Gold","XAU"],
    "XAG_USD":    ["USD","Silver","XAG"],
    "XPD_USD":    ["USD","Palladium"],
    "NATGAS_USD": ["USD","Gas","EIA","Energy"],
    "WTICO_USD":  ["USD","Oil","EIA","OPEC","Crude"],
    "CORN_USD":   ["USD","USDA","Corn","Grain"],
    "SUGAR_USD":  ["USD","Sugar"],
    "WHEAT_USD":  ["USD","USDA","Wheat","Grain"],
    "SOYBN_USD":  ["USD","USDA","Soy","Grain"],
    "SPX500_USD": ["USD","SP500","Fed","FOMC","CPI","NFP","GDP"],
    "NAS100_USD": ["USD","Fed","FOMC","CPI","NFP","GDP"],
    "UK100_GBP":  ["GBP","BOE","UK","CPI","GDP"],
    "DE30_EUR":   ["EUR","ECB","Germany","CPI","GDP"],
}

UNIVERSAL_KEYWORDS = [
    "Non-Farm","NFP","FOMC","Federal Reserve","Fed Rate",
    "Interest Rate Decision","CPI","Inflation","GDP","Employment Change",
    "Unemployment","ECB","BOE","BOJ","RBA","SNB","RBNZ",
    "Jackson Hole","Powell","Lagarde","Central Bank",
    "Quantitative","Rate Decision","Monetary Policy",
]

_cache = {"data": [], "fetched_at": None}


def _try_trading_economics():
    try:
        now = datetime.utcnow()
        from_date = now.strftime("%Y-%m-%d")
        to_date   = (now + timedelta(days=7)).strftime("%Y-%m-%d")
        url = (f"https://api.tradingeconomics.com/calendar/country/all"
               f"/{from_date}/{to_date}?c=guest:guest&f=json")
        req = urllib.request.Request(
            url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        events = []
        for e in data:
            imp = str(e.get("importance","0"))
            if imp not in ("2","3","high","High","3.0","2.0"):
                continue
            events.append({
                "title":   e.get("event",""),
                "country": e.get("country",""),
                "date":    e.get("date","")[:10] if e.get("date") else "",
                "time":    e.get("date","")[11:16] if e.get("date","") and len(e.get("date",""))>10 else "",
                "impact":  "High",
                "source":  "trading_economics",
            })
        if events:
            logger.info(f"Trading Economics: {len(events)} events")
        return events
    except Exception as e:
        logger.debug(f"Trading Economics failed: {e}")
        return []


def _try_forexfactory():
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]
    events = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
                "Accept":          "application/json, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer":         "https://www.forexfactory.com/",
                "Cache-Control":   "no-cache",
            })
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read())
            high = [e for e in data if e.get("impact") == "High"]
            events.extend(high)
        except urllib.error.HTTPError as e:
            logger.debug(f"ForexFactory HTTP {e.code}")
        except Exception as e:
            logger.debug(f"ForexFactory error: {e}")
    if events:
        logger.info(f"ForexFactory: {len(events)} events")
    return events


def _get_smart_schedule():
    now     = datetime.utcnow()
    today   = now.strftime("%Y-%m-%d")
    weekday = now.weekday()
    events  = []
    if weekday < 5:
        events.append({"title":"US Economic Data Release Window","country":"USD",
                        "impact":"High","date":today,"time":"13:30","source":"schedule"})
        events.append({"title":"US Economic Data / Fed Speakers","country":"USD",
                        "impact":"High","date":today,"time":"15:00","source":"schedule"})
    if weekday == 2:
        events.append({"title":"EIA Crude Oil Inventories","country":"USD",
                        "impact":"High","date":today,"time":"15:30","source":"schedule"})
    if weekday == 3:
        events.append({"title":"US Initial Jobless Claims","country":"USD",
                        "impact":"High","date":today,"time":"13:30","source":"schedule"})
    if weekday == 4:
        events.append({"title":"Non-Farm Payrolls (NFP) — potential release","country":"USD",
                        "impact":"High","date":today,"time":"13:30","source":"schedule"})
    return events


def _parse_time(event):
    date_str = event.get("date","")
    time_str = event.get("time","")
    if not date_str:
        return None
    try:
        if "T" in date_str:
            return datetime.strptime(date_str[:16], "%Y-%m-%dT%H:%M")
        if time_str and ":" in time_str:
            for fmt in ["%m-%d-%Y %I:%M%p","%Y-%m-%d %H:%M"]:
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", fmt)
                    if "am" in time_str.lower() or "pm" in time_str.lower():
                        dt = dt + timedelta(hours=5)
                    return dt
                except ValueError:
                    continue
        for fmt in ["%Y-%m-%d","%m-%d-%Y","%m/%d/%Y"]:
            try:
                return datetime.strptime(date_str, fmt).replace(hour=13, minute=30)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def _affects(event, instrument):
    title   = event.get("title","").upper()
    country = event.get("country","").upper()
    for kw in UNIVERSAL_KEYWORDS:
        if kw.upper() in title:
            return True
    for token in INSTRUMENT_CURRENCIES.get(instrument, ["USD"]):
        if token.upper() in title or token.upper() in country:
            return True
    return False


def _fetch():
    global _cache
    now = datetime.utcnow()
    if (_cache["fetched_at"] and
            (now - _cache["fetched_at"]).total_seconds() < CACHE_TTL_MINUTES * 60
            and _cache["data"]):
        return _cache["data"]

    events = _try_trading_economics()
    if not events:
        events = _try_forexfactory()
        if not events:
            logger.warning("All live news sources failed — using smart schedule only")

    events.extend(_get_smart_schedule())

    seen, unique = set(), []
    for e in events:
        key = f"{e.get('title','')[:30]}{e.get('date','')}"
        if key not in seen:
            seen.add(key); unique.append(e)

    _cache = {"data": unique, "fetched_at": now}
    logger.info(f"News cache: {len(unique)} events")
    return unique


def check_news_blackout(instrument):
    now    = datetime.utcnow()
    events = _fetch()
    blocked_event = None
    min_delta     = None
    upcoming      = []

    for event in events:
        if not _affects(event, instrument):
            continue
        et = _parse_time(event)
        if not et:
            continue
        delta = (et - now).total_seconds() / 60

        if 0 < delta <= 1440:
            upcoming.append({
                "title":       event.get("title",""),
                "country":     event.get("country",""),
                "time_utc":    et.strftime("%H:%M UTC"),
                "date":        et.strftime("%Y-%m-%d"),
                "minutes":     int(delta),
                "in_blackout": delta <= BLACKOUT_MINUTES,
                "source":      event.get("source",""),
            })

        if -BLACKOUT_MINUTES <= delta <= BLACKOUT_MINUTES:
            if min_delta is None or abs(delta) < abs(min_delta):
                min_delta     = delta
                blocked_event = event

    upcoming.sort(key=lambda x: x["minutes"])

    if blocked_event:
        et_b   = _parse_time(blocked_event)
        title  = blocked_event.get("title","High-impact event")
        d      = int(min_delta)
        timing = f"in {d} min" if d >= 0 else f"{abs(d)} min ago"
        return {
            "blocked":          True,
            "reason":           f"News blackout — {title} ({timing})",
            "event_title":      title,
            "event_time":       et_b.strftime("%H:%M UTC") if et_b else "—",
            "minutes_to_event": d,
            "upcoming_events":  upcoming[:5],
        }

    return {
        "blocked":          False,
        "reason":           "",
        "event_title":      "",
        "event_time":       "",
        "minutes_to_event": None,
        "upcoming_events":  upcoming[:5],
    }


def get_all_upcoming_events(hours=24):
    now    = datetime.utcnow()
    events = _fetch()
    result = []
    seen   = set()
    for event in events:
        et = _parse_time(event)
        if not et:
            continue
        delta = (et - now).total_seconds() / 60
        if 0 < delta <= hours * 60:
            title = event.get("title","")
            key   = f"{title[:30]}{et.strftime('%Y-%m-%d%H')}"
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "title":       title,
                "country":     event.get("country",""),
                "impact":      "High",
                "time_utc":    et.strftime("%Y-%m-%d %H:%M UTC"),
                "minutes":     int(delta),
                "in_blackout": delta <= BLACKOUT_MINUTES,
                "source":      event.get("source",""),
            })
    result.sort(key=lambda x: x["minutes"])
    return result#   v 3  
 