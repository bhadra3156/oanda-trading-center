# FILE: backend/keepalive.py
# ─────────────────────────────────────────────────────────────────────────────
# Prevents Render free tier cold starts by self-pinging every 10 minutes.
# Add this to your main.py startup.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import httpx
import logging
import os

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "https://oanda-trading-center.onrender.com")
PING_INTERVAL_SECONDS = 600  # every 10 minutes


async def keepalive_loop():
    """
    Self-ping the backend every 10 minutes so Render never
    spins it down. Render free tier sleeps after 15min of inactivity.
    """
    await asyncio.sleep(30)  # wait for startup to complete first
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{BACKEND_URL}/")
                logger.info(f"[keepalive] ping ok — status {resp.status_code}")
        except Exception as e:
            logger.warning(f"[keepalive] ping failed: {e}")
        await asyncio.sleep(PING_INTERVAL_SECONDS)