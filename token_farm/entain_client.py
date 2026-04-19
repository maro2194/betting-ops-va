"""Entain (Ladbrokes / Neds) API client — SGM pricing + placement via REST.

Flow:
  1. Login via Patchright browser ONCE (~30s) -> captures session cookies
  2. All subsequent calls use those cookies as plain HTTP (no browser)

Reverse-engineered from HAR capture 2026-04-18.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from patchright.async_api import async_playwright

logger = logging.getLogger("entain")

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"'

BASES = {
    "neds":      ("https://www.neds.com.au",      "https://api.neds.com.au"),
    "ladbrokes": ("https://www.ladbrokes.com.au", "https://api.ladbrokes.com.au"),
}

PRODUCT_FIXED_WIN = "940b8704-e497-4a76-b390-00918ff7d282"
CATEGORY_BASKETBALL_NBA = "4d54ccd1-17b0-40c3-a7e2-be08ced1e7d0"  # seen in HAR place-bet


class EntainClient:
    """Session-based Entain client. Call login() once, then use the API methods."""

    def __init__(self, brand: str = "neds", cache_path: str | None = None,
                 cache_max_age_sec: int = 3600):
        assert brand in BASES, f"unknown brand {brand!r}"
        self.brand = brand
        self.web_base, self.api_base = BASES[brand]
        self.cookies: dict[str, str] = {}
        self.device_id: str = str(uuid.uuid4())
        self.client_id: str | None = None  # server-side account id
        self._http: httpx.AsyncClient | None = None
        self.cache_path = cache_path or f"/tmp/entain_session_{brand}.json"
        self.cache_max_age_sec = cache_max_age_sec

    def _load_cache(self, username: str) -> bool:
        """Load cookies+device_id from disk if fresh for this user. Returns True on hit."""
        try:
            p = Path(self.cache_path)
            if not p.exists():
                return False
            data = json.loads(p.read_text())
            age = time.time() - data.get("saved_at", 0)
            if age > self.cache_max_age_sec:
                logger.info(f"cache stale ({age:.0f}s > {self.cache_max_age_sec}s)")
                return False
            if data.get("username") != username:
                return False
            self.cookies = data["cookies"]
            self.device_id = data["device_id"]
            logger.info(f"cache hit ({age:.0f}s old, {len(self.cookies)} cookies)")
            return True
        except Exception as e:
            logger.warning(f"cache load error: {e!r}")
            return False

    def _save_cache(self, username: str) -> None:
        try:
            Path(self.cache_path).write_text(json.dumps({
                "saved_at": time.time(),
                "username": username,
                "device_id": self.device_id,
                "cookies": self.cookies,
            }))
            logger.info(f"cache saved -> {self.cache_path}")
        except Exception as e:
            logger.warning(f"cache save error: {e!r}")

    async def _session_is_valid(self) -> bool:
        """Quick check: hit a cheap authenticated endpoint."""
        try:
            c = self._client()
            r = await c.post(f"{self.api_base}/v2/client-notifications/list?",
                             json={"filter": {"exclude_types": ["BonusBalance"]}})
            return r.status_code == 200
        except Exception:
            return False

    # -------- login (cache-aware, browser fallback) --------
    async def login(self, username: str, password: str, force_browser: bool = False,
                    skip_validation: bool = True) -> dict:
        """Use cached cookies if fresh; else browser login.
        skip_validation=True trusts the cache's age alone (cookies are typically
        valid for hours). Set False for a probe request on every login."""
        if not force_browser and self._load_cache(username):
            if skip_validation or await self._session_is_valid():
                return {"success": True, "cookies": list(self.cookies.keys()),
                        "device_id": self.device_id, "source": "cache"}
            logger.info("cached session invalid, falling back to browser")
            self.cookies = {}
            if self._http:
                await self._http.aclose()
                self._http = None

        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(
                channel="chrome", headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"
                ),
                viewport={"width": 1400, "height": 900},
                locale="en-AU", timezone_id="Australia/Sydney",
            )
            page = await ctx.new_page()

            # Capture access_token from /oauth2/token response
            tokens: list[dict] = []
            async def on_resp(r):
                try:
                    if "oauth2/token" in r.url and r.status == 200:
                        text = await r.text()
                        if "access_token" in text:
                            tokens.append(json.loads(text))
                except Exception:
                    pass
            page.on("response", on_resp)

            await page.goto(self.web_base + "/", wait_until="commit", timeout=60000)
            await page.wait_for_timeout(8000)

            # cookie banner
            for cs in [page.get_by_text("Accept All", exact=True),
                       page.locator("#onetrust-accept-btn-handler")]:
                try:
                    if await cs.count() and await cs.first.is_visible():
                        await cs.first.click(timeout=5000)
                        await page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            # click Log in
            clicked = False
            for _ in range(3):
                for sel in [page.get_by_text("Log in", exact=True),
                            page.get_by_text("Login", exact=True)]:
                    try:
                        ct = await sel.count()
                        for i in range(min(ct, 5)):
                            if await sel.nth(i).is_visible():
                                await sel.nth(i).click(timeout=5000)
                                clicked = True; break
                    except Exception:
                        pass
                    if clicked: break
                if clicked: break
                await page.wait_for_timeout(3000)

            await page.wait_for_timeout(6000)
            await page.locator("#username").wait_for(state="visible", timeout=15000)
            await page.locator("#username").fill(username)
            await page.locator("#password").fill(password)
            await page.wait_for_timeout(1500)
            try:
                async with page.expect_navigation(timeout=45000, wait_until="commit"):
                    await page.click("#accept")
            except Exception:
                pass
            await page.wait_for_timeout(8000)

            logged_in = "/auth/" not in page.url
            if not logged_in:
                raise RuntimeError(f"login failed, still on {page.url}")

            # Grab cookies
            all_cookies = await ctx.cookies()
            for c in all_cookies:
                self.cookies[c["name"]] = c["value"]

            # Try to read Device-Id from localStorage (browser stores it)
            try:
                dev = await page.evaluate(
                    "() => localStorage.getItem('deviceId') || "
                    "localStorage.getItem('device_id') || "
                    "localStorage.getItem('deviceID')"
                )
                if dev:
                    self.device_id = dev
                    logger.info(f"Device-Id from localStorage: {dev}")
            except Exception:
                pass

            await browser.close()
        finally:
            await pw.stop()

        logger.info(f"login OK, {len(self.cookies)} cookies, device_id={self.device_id[:8]}...")
        self._save_cache(username)
        return {"success": True, "cookies": list(self.cookies.keys()),
                "device_id": self.device_id, "source": "browser",
                "tokens": [{"type": t.get("token_type"), "exp": t.get("expires_in")} for t in tokens]}

    # -------- HTTP session --------
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                cookies=self.cookies,
                headers={
                    "User-Agent": CHROME_UA,
                    "sec-ch-ua": SEC_CH_UA,
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "Referer": self.web_base + "/",
                    "Origin": self.web_base,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Device-Id": self.device_id,
                },
                timeout=30.0,
            )
        return self._http

    async def close(self):
        if self._http:
            await self._http.aclose()

    # -------- Account / balance --------
    async def get_balance(self) -> dict:
        c = self._client()
        # client_id is required for balance; derive once from toolbox/balances probe
        body = {"client_id": self.client_id} if self.client_id else {}
        r = await c.post(f"{self.api_base}/v2/toolbox/balances?", json=body)
        data = r.json()
        return {"status": r.status_code, "data": data}

    # -------- Event discovery --------
    async def get_event_card(self, event_id: str) -> dict:
        c = self._client()
        r = await c.get(f"{self.api_base}/v2/sport/event-card?id={event_id}")
        r.raise_for_status()
        return r.json()

    # -------- SGM quote --------
    async def get_sgm_quote(self, event_id: str, selections: list[dict]) -> dict:
        """selections = [{'market_id': ..., 'entrant_id': ...}, ...]"""
        c = self._client()
        payload = {event_id: {"event_id": event_id, "selections": selections}}
        qs = quote(json.dumps(payload))
        url = f"{self.api_base}/v2/same-game-multi/get-odds?same_game_multies={qs}"
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

    # -------- Cross-game multi (no correlation, raw multiplication) --------
    async def place_cross_game_multi(self, *, legs: list[dict], stake: float,
                                      root_category_id: str = CATEGORY_BASKETBALL_NBA) -> dict:
        """Place a parlay across MULTIPLE games.

        Each leg: {event_id, market_id, entrant_id, entrant_name, odds:{num,den}}
        No SGM quote needed; combined odds = product of leg odds.
        """
        import uuid as _u
        c = self._client()
        payload_legs = []
        for leg in legs:
            o = leg["odds"]
            payload_legs.append({
                "bet_id": leg.get("bet_id") or str(_u.uuid4()),
                "entrant_name": leg["entrant_name"],
                "odds": {
                    "numerator": o["numerator"],
                    "denominator": o["denominator"],
                    "decimal": round(1 + o["numerator"] / o["denominator"], 2),
                },
                "product_type_id": PRODUCT_FIXED_WIN,
                "root_category_id": root_category_id,
                "selections": [{
                    "position": 1,
                    "market_id": leg["market_id"],
                    "event_id": leg["event_id"],
                    "entrant_id": leg["entrant_id"],
                }],
            })
        body = {
            "stake": float(stake),
            "bets": [{"legs": payload_legs, "prices": {}}],  # cross-game: empty prices
        }
        r = await c.post(f"{self.api_base}/v2/betting/place-bet?", json=body)
        return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}

    # -------- SGM place --------
    async def place_sgm(self, *, event_id: str, legs: list[dict], stake: float,
                        quoted_odds: dict, root_category_id: str = CATEGORY_BASKETBALL_NBA) -> dict:
        """Each leg: {bet_id, entrant_name, odds:{num,den}, market_id, entrant_id}
        quoted_odds: {'numerator': N, 'denominator': D} from get_sgm_quote
        """
        c = self._client()
        payload_legs = []
        for leg in legs:
            payload_legs.append({
                "bet_id": leg["bet_id"],
                "entrant_name": leg["entrant_name"],
                "odds": leg["odds"],  # {numerator, denominator}
                "product_type_id": PRODUCT_FIXED_WIN,
                "root_category_id": root_category_id,
                "selections": [{
                    "position": 1,
                    "market_id": leg["market_id"],
                    "event_id": event_id,
                    "entrant_id": leg["entrant_id"],
                }],
            })
        body = {
            "stake": float(stake),
            "bets": [{
                "legs": payload_legs,
                "prices": {event_id: {"valid": True, "odds": quoted_odds}},
            }],
        }
        r = await c.post(f"{self.api_base}/v2/betting/place-bet?", json=body)
        return {"status": r.status_code, "body": r.json() if r.status_code == 200 else r.text}
