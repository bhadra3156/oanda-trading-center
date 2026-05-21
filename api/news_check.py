def _fetch_calendar() -> list:
    """Fetch high-impact events — tries multiple free sources."""
    global _news_cache
    now = datetime.utcnow()

    if (_news_cache["fetched_at"] and
            (now - _news_cache["fetched_at"]).seconds < _CACHE_TTL_MINUTES * 120):
        return _news_cache["data"]

    # Multiple free sources to try in order
    sources = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]

    events = []
    for url in sources:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; TradingBot/1.0)",
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                }
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
                high = [e for e in data if e.get("impact") == "High"]
                events.extend(high)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning(f"Rate limited by {url} — using cached data")
                # Return stale cache rather than empty
                if _news_cache["data"]:
                    return _news_cache["data"]
            else:
                logger.warning(f"HTTP {e.code} from {url}")
        except Exception as e:
            logger.warning(f"Calendar fetch error: {e}")

    # If we got nothing, add hardcoded weekly high-impact times as fallback
    if not events:
        events = _get_hardcoded_events()

    _news_cache = {"data": events, "fetched_at": now}
    return events


def _get_hardcoded_events() -> list:
    """
    Fallback hardcoded schedule of typical weekly high-impact events.
    These are the MOST DANGEROUS times to trade — always block them.
    All times UTC.
    """
    now = datetime.utcnow()
    weekday = now.weekday()  # 0=Mon, 4=Fri
    events = []

    # NFP — First Friday of month at 13:30 UTC
    if weekday == 4:
        events.append({
            "title": "Non-Farm Payrolls (scheduled)",
            "country": "USD", "impact": "High",
            "date": now.strftime("%Y-%m-%d"), "time": "1:30pm"
        })

    # FOMC — check if Wednesday 19:00 UTC (approximate)
    if weekday == 2:
        events.append({
            "title": "FOMC Minutes / Fed Statement (scheduled)",
            "country": "USD", "impact": "High",
            "date": now.strftime("%Y-%m-%d"), "time": "7:00pm"
        })

    # Weekly: every day 13:30 UTC has potential US data
    events.append({
        "title": "US Economic Data Window",
        "country": "USD", "impact": "High",
        "date": now.strftime("%Y-%m-%d"), "time": "1:30pm"
    })

    return events