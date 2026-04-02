"""
Persistent bet365 browser service.
Manages a Camoufox browser that stays alive for fast bet placement.
Runs as an asyncio background task within the FastAPI app.
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("bet365_service")

# ─── Config from env ────────────────────────────────────────────────────────

BET365_USERNAME = os.getenv("BET365_USERNAME", "")
BET365_PASSWORD = os.getenv("BET365_PASSWORD", "")
BET365_PROXY_HOST = os.getenv("BET365_PROXY_HOST", "")
BET365_PROXY_PORT = os.getenv("BET365_PROXY_PORT", "12323")
BET365_PROXY_USER = os.getenv("BET365_PROXY_USER", "")
BET365_PROXY_PASS = os.getenv("BET365_PROXY_PASS", "")
BET365_UNIT_SIZE = float(os.getenv("BET365_UNIT_SIZE", "1"))

CAMOUFOX_EXE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camoufox", "camoufox.exe")

# ─── Sport/stat mapping ─────────────────────────────────────────────────────

SPORT_NAV_MAP = {
    "NBA": "Basketball", "MLB": "Baseball", "NFL": "American Football",
    "AFL": "Australian Rules", "NRL": "Rugby League",
    "Soccer": "Soccer", "Tennis": "Tennis", "Cricket": "Cricket",
}

SPORT_COMP_MAP = {
    "NBA": "NBA", "MLB": "MLB", "NFL": "NFL", "AFL": "AFL", "NRL": "NRL",
}

STAT_TO_TAB = {
    "rebounds": "Rebounds", "points": "Points", "assists": "Assists",
    "3 pointers": "Threes", "three pointers": "Threes", "threes": "Threes",
    "steals": "Defence", "blocks": "Defence", "turnovers": "Defence",
    "disposals": "Player", "marks": "Player", "tackles": "Player",
    "goals": "Player", "total bases": "Player", "strikeouts": "Player",
    "hits": "Hits", "runs": "Runs", "rbis": "RBIs",
    "combos": "Combos",
}

STAT_MARKET_NAMES = {
    "rebounds": ["Rebounds", "Player Rebounds"],
    "points": ["Points", "Player Points"],
    "assists": ["Assists", "Player Assists"],
    "3 pointers": ["3 Pointers", "Three Pointers", "Threes Made"],
    "three pointers": ["3 Pointers", "Three Pointers", "Threes Made"],
    "threes": ["Threes Made", "3 Pointers"],
    "steals": ["Steals"], "blocks": ["Blocks"], "turnovers": ["Turnovers"],
    "disposals": ["Disposals", "Player Disposals"],
    "marks": ["Marks"], "tackles": ["Tackles"], "goals": ["Goals", "Anytime Goalscorer"],
    "total bases": ["Total Bases", "Batter Total Bases"],
    "hits": ["Hits", "Player Hits"], "runs": ["Runs", "Player Runs"],
    "strikeouts": ["Strikeouts", "Pitcher Strikeouts"],
}

# Timing delays (seconds)
DELAY_SPORT_NAV = 3
DELAY_COMP_NAV = 4
DELAY_MATCH_CLICK = 5
DELAY_TAB_CLICK = 12
DELAY_SCROLL = 0.4
DELAY_BETSLIP_OPEN = 3
DELAY_STAKE_SETTLE = 2
DELAY_PLACE_BET = 5
DELAY_ODDS_CHANGE_ACCEPT = 5


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class PlacementResult:
    success: bool
    dry_run: bool = False
    odds: str = ""
    stake: float = 0
    potential_return: str = ""
    error: str = ""
    screenshot: str = ""


# ─── Bet365 Browser Service ─────────────────────────────────────────────────

class Bet365Service:
    """Manages a persistent Camoufox browser for bet365 operations."""

    def __init__(self):
        self._browser = None
        self._page = None
        self._logged_in = False
        self._lock = asyncio.Lock()
        self._last_activity = 0
        self._balance: Optional[str] = None

    @property
    def is_alive(self) -> bool:
        return self._page is not None and self._logged_in

    @property
    def status(self) -> dict:
        return {
            "alive": self.is_alive,
            "logged_in": self._logged_in,
            "balance": self._balance,
            "last_activity": self._last_activity,
            "idle_seconds": int(time.time() - self._last_activity) if self._last_activity else 0,
        }

    async def start(self) -> bool:
        """Launch browser and login to bet365."""
        async with self._lock:
            if self.is_alive:
                logger.info("Browser already alive, skipping start")
                return True
            try:
                return await self._launch_and_login()
            except Exception as e:
                logger.error(f"Failed to start bet365 service: {e}")
                await self._cleanup()
                return False

    async def stop(self):
        """Shutdown browser."""
        async with self._lock:
            await self._cleanup()

    async def place_pick(self, pick: dict) -> dict:
        """
        Place a single pick on bet365.

        pick dict keys:
            sport (str): "NBA", "MLB", "AFL", etc.
            player (str): "Jalen Green"
            stat (str): "rebounds", "points", etc.
            line (float): 4.5
            side (str): "Over" or "Under"
            odds (float): expected odds
            units (float): number of units
        """
        async with self._lock:
            if not self.is_alive:
                # Try to restart
                logger.info("Browser not alive, attempting restart...")
                if not await self._launch_and_login():
                    return {"success": False, "error": "Browser not running and restart failed"}

            self._last_activity = time.time()

            try:
                result = await self._place_pick_internal(pick)
                # Update balance after placement
                self._balance = await self._read_balance()
                return result
            except Exception as e:
                logger.error(f"Placement error: {e}")
                return {"success": False, "error": str(e)}

    async def get_balance(self) -> Optional[str]:
        """Read current balance."""
        if not self.is_alive:
            return None
        try:
            self._balance = await self._read_balance()
            return self._balance
        except Exception:
            return self._balance

    # ─── Internal methods ────────────────────────────────────────────────

    async def _launch_and_login(self) -> bool:
        """Launch Camoufox and login to bet365."""
        from camoufox.async_api import AsyncCamoufox

        proxy = {
            "server": f"http://{BET365_PROXY_HOST}:{BET365_PROXY_PORT}",
            "username": BET365_PROXY_USER,
            "password": BET365_PROXY_PASS,
        }

        use_headed = sys.platform == "linux"
        if use_headed:
            import subprocess
            try:
                subprocess.run(["pgrep", "-x", "Xvfb"], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1080x24"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                os.environ["DISPLAY"] = ":99"
                await asyncio.sleep(1)
            os.environ.setdefault("DISPLAY", ":99")

        launch_kwargs = dict(
            headless=not use_headed,
            proxy=proxy,
            geoip=True,
        )
        if sys.platform == "win32" and os.path.exists(CAMOUFOX_EXE):
            launch_kwargs["executable_path"] = CAMOUFOX_EXE

        logger.info("Launching Camoufox browser...")
        # We use the context manager internally but store the reference
        self._browser = await AsyncCamoufox(**launch_kwargs).__aenter__()
        self._page = await self._browser.new_page()

        # Login
        logger.info("Navigating to bet365.com.au...")
        await self._page.goto("https://www.bet365.com.au/", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # Check if already logged in
        cookies = {c["name"]: c["value"] for c in await self._page.context.cookies()}
        if "lgs=1" in cookies.get("session", ""):
            logger.info("Already logged in from previous session")
            self._logged_in = True
            self._last_activity = time.time()
            self._balance = await self._read_balance()
            return True

        # Click Log In
        for attempt in range(3):
            logger.info(f"Login attempt {attempt + 1}/3...")
            try:
                await self._page.locator('text="Log In"').first.click()
                await asyncio.sleep(3)

                await self._page.locator('input[placeholder*="Username"]').fill(BET365_USERNAME)
                await asyncio.sleep(0.3)
                await self._page.locator('input[type="password"]').fill(BET365_PASSWORD)
                await asyncio.sleep(0.5)
                await self._page.locator('.slm2-2').click()
                await asyncio.sleep(10)

                cookies = {c["name"]: c["value"] for c in await self._page.context.cookies()}
                if "lgs=1" in cookies.get("session", ""):
                    logger.info("LOGIN SUCCESS")
                    self._logged_in = True
                    self._last_activity = time.time()
                    self._balance = await self._read_balance()
                    return True

                logger.warning(f"Login attempt {attempt + 1} failed, retrying...")
                await self._page.goto("https://www.bet365.com.au/", wait_until="networkidle", timeout=30000)
                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"Login attempt {attempt + 1} error: {e}")
                await asyncio.sleep(5)

        logger.error("All login attempts failed")
        return False

    async def _cleanup(self):
        """Close browser resources."""
        self._logged_in = False
        try:
            if self._browser:
                await self._browser.__aexit__(None, None, None)
        except Exception:
            pass
        self._browser = None
        self._page = None

    async def _read_balance(self) -> Optional[str]:
        """Read balance from page header."""
        if not self._page:
            return None
        try:
            result = await self._page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('*')) {
                        const t = (el.innerText || '').trim();
                        if (t.match(/^\\$[\\d,.]+$/) && t.length < 15 && el.offsetParent) {
                            const rect = el.getBoundingClientRect();
                            if (rect.top < 100) return t;
                        }
                    }
                    return null;
                }
            """)
            return result
        except Exception:
            return None

    async def _place_pick_internal(self, pick: dict) -> dict:
        """Full placement flow for a single pick."""
        page = self._page
        sport = pick["sport"].upper().strip()
        player = pick["player"]
        stat = pick["stat"].lower().strip()
        line = float(pick["line"])
        side = pick["side"]
        expected_odds = float(pick.get("odds", 0))
        stake = int(pick.get("units", 1) * BET365_UNIT_SIZE)
        if stake < 1:
            stake = 1

        base = {
            "player": player, "stat": stat, "line": line,
            "side": side, "expected_odds": expected_odds,
        }

        logger.info(f"Placing: {player} {side} {line} {stat} @ ${stake} ({sport})")

        # Step 1: Clear betslip
        await self._clear_betslip()

        # Step 2: Navigate to sport
        if not await self._navigate_to_sport(sport):
            return {**base, "success": False, "error": f"Could not navigate to {sport}"}

        # Step 3: Find and click match
        team = self._find_player_team(player, sport)
        if not team:
            return {**base, "success": False, "error": f"No team mapping for {player}"}

        if not await self._click_match(team):
            return {**base, "success": False, "error": f"Match with '{team}' not found"}

        # Step 4: Click market tab
        tab_name = STAT_TO_TAB.get(stat, "Player")
        if not await self._click_market_tab(tab_name):
            for fb in ["Player", "Player Props", stat.title()]:
                if fb != tab_name and await self._click_market_tab(fb):
                    break
            else:
                return {**base, "success": False, "error": f"Tab '{tab_name}' not found"}

        # Step 5: Expand market section
        variants = STAT_MARKET_NAMES.get(stat, [stat.title()])
        for variant in variants:
            try:
                el = page.locator(f'text=/{re.escape(variant)}/i').first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await asyncio.sleep(3)
                    break
            except Exception:
                continue

        # Step 6: Find player odds
        odds_info = await self._find_player_odds(player, line, side)
        if not odds_info:
            # Scroll and retry
            for _ in range(5):
                await page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(DELAY_SCROLL)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(1)
            odds_info = await self._find_player_odds(player, line, side)

        if not odds_info:
            return {**base, "success": False, "error": f"Player '{player}' not found on page"}

        actual_odds = float(odds_info["odds"])
        logger.info(f"Found {player}: {odds_info['odds']} @ ({odds_info['x']}, {odds_info['y']})")

        # Step 7: Click odds to open betslip
        if not await self._click_odds(odds_info["x"], odds_info["y"]):
            await asyncio.sleep(1)
            if not await self._click_odds(odds_info["x"], odds_info["y"]):
                return {**base, "success": False, "error": "Betslip did not open"}

        # Step 8: Enter stake
        if not await self._enter_stake(stake):
            return {**base, "success": False, "error": "Could not enter stake"}

        # Step 9: Place bet
        result = await self._place_bet()

        return {
            **base,
            "success": result.success,
            "actual_odds": odds_info["odds"],
            "stake": stake,
            "potential_return": result.potential_return,
            "error": result.error,
            "screenshot": result.screenshot,
        }

    # ─── DOM interaction methods ─────────────────────────────────────────

    async def _navigate_to_sport(self, sport: str) -> bool:
        page = self._page
        sidebar_name = SPORT_NAV_MAP.get(sport, sport)

        try:
            await page.goto("https://www.bet365.com.au/#/HO/", wait_until="networkidle", timeout=20000)
            await asyncio.sleep(2)
        except Exception:
            pass

        try:
            await page.locator('text="All Sports"').first.click()
            await asyncio.sleep(2)
        except Exception:
            return False

        try:
            await page.locator(f'text="{sidebar_name}"').first.click()
            await asyncio.sleep(DELAY_SPORT_NAV)
        except Exception:
            return False

        comp = SPORT_COMP_MAP.get(sport)
        if comp:
            try:
                for link in await page.locator(f'text="{comp}"').all():
                    if await link.is_visible():
                        await link.click()
                        await asyncio.sleep(DELAY_COMP_NAV)
                        break
            except Exception:
                pass

        return True

    async def _click_match(self, team_name: str) -> bool:
        page = self._page
        team_lower = team_name.lower()

        for scroll_pass in range(5):
            if scroll_pass > 0:
                await page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(DELAY_SCROLL)

            body_text = await page.inner_text("body")
            if team_lower not in body_text.lower():
                continue

            try:
                el = page.locator(f'text=/{re.escape(team_name)}/i').first
                if await el.is_visible(timeout=3000):
                    await el.click()
                    await asyncio.sleep(DELAY_MATCH_CLICK)
                    return True
            except Exception:
                pass

        return False

    async def _click_market_tab(self, tab_name: str) -> bool:
        page = self._page
        try:
            for tab in await page.locator(f'text="{tab_name}"').all():
                if await tab.is_visible():
                    await tab.click()
                    await asyncio.sleep(DELAY_TAB_CLICK)
                    for _ in range(6):
                        await page.evaluate("window.scrollBy(0, 500)")
                        await asyncio.sleep(DELAY_SCROLL)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(1)
                    return True
        except Exception:
            pass
        return False

    async def _find_player_odds(self, player_name: str, line: float, side: str) -> Optional[dict]:
        page = self._page
        player_lower = player_name.lower().strip()
        last_name = player_lower.split()[-1]
        first_initial = player_lower[0].upper() if player_lower else ""
        abbreviated = f"{first_initial}. {last_name.capitalize()}"

        result = await page.evaluate("""
            (args) => {
                const { playerLower, lastName, abbreviated, lineStr, sideLower } = args;
                const allEls = document.querySelectorAll('*');
                let playerEl = null;

                for (const el of allEls) {
                    if (!el.offsetParent || el.children.length > 2) continue;
                    const text = (el.innerText || '').trim().toLowerCase();
                    if (text.length > 50) continue;
                    if (text.includes(playerLower) || text === abbreviated.toLowerCase()) {
                        playerEl = el; break;
                    }
                }

                if (!playerEl) {
                    let matches = [];
                    for (const el of allEls) {
                        if (!el.offsetParent || el.children.length > 2) continue;
                        const text = (el.innerText || '').trim().toLowerCase();
                        if (text.length > 40) continue;
                        if (text.includes(lastName) && text.length < 35) matches.push(el);
                    }
                    if (matches.length === 1) playerEl = matches[0];
                    else if (matches.length > 1) {
                        for (const el of matches) {
                            if ((el.innerText || '').trim().toLowerCase().startsWith(playerLower[0])) {
                                playerEl = el; break;
                            }
                        }
                        if (!playerEl) playerEl = matches[0];
                    }
                }

                if (!playerEl) return { found: false };

                const pRect = playerEl.getBoundingClientRect();
                const rowOdds = [];
                for (const el of allEls) {
                    const t = (el.innerText || '').trim();
                    if (!t.match(/^\\d+\\.\\d{2}$/) || !el.offsetParent || el.children.length !== 0) continue;
                    const r = el.getBoundingClientRect();
                    if (Math.abs(r.y - pRect.y) < 35 && r.x > pRect.x) {
                        rowOdds.push({ odds: t, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), rawX: r.x });
                    }
                }
                rowOdds.sort((a, b) => a.rawX - b.rawX);

                let target = null;
                if (rowOdds.length >= 2) {
                    target = sideLower === "over" ? rowOdds[0] : rowOdds[rowOdds.length - 1];
                } else if (rowOdds.length === 1) {
                    target = rowOdds[0];
                }

                return { found: true, player: playerEl.innerText.trim(), target: target, allOdds: rowOdds };
            }
        """, {
            "playerLower": player_lower,
            "lastName": last_name,
            "abbreviated": abbreviated,
            "lineStr": str(line),
            "sideLower": side.lower().strip(),
        })

        if not result.get("found") or not result.get("target"):
            return None

        t = result["target"]
        return {"player": result["player"], "odds": t["odds"], "x": t["x"], "y": t["y"]}

    async def _click_odds(self, x: int, y: int) -> bool:
        await self._page.mouse.click(x, y)
        await asyncio.sleep(DELAY_BETSLIP_OPEN)
        body = await self._page.inner_text("body")
        return "stake" in body.lower() or "bet slip" in body.lower()

    async def _enter_stake(self, amount: int) -> bool:
        page = self._page
        try:
            inputs = await page.locator("input").all()
            for inp in reversed(inputs):
                if not await inp.is_visible():
                    continue
                inp_type = (await inp.get_attribute("type") or "").lower()
                if inp_type in ("text", "number", "tel", ""):
                    await inp.click()
                    await asyncio.sleep(0.3)
                    await inp.fill(str(amount))
                    await asyncio.sleep(DELAY_STAKE_SETTLE)
                    return True
        except Exception as e:
            logger.error(f"Stake entry failed: {e}")
        return False

    async def _place_bet(self) -> PlacementResult:
        page = self._page
        ts = int(time.time())
        screenshot_path = f"/tmp/b365_place_{ts}.png"
        body = await page.inner_text("body")

        odds_match = re.search(r'@\s*(\d+\.\d{2})', body)
        betslip_odds = odds_match.group(1) if odds_match else ""
        ret_match = re.search(r'(?:To Return|Returns?|Potential Returns?)\s*\$?([\d,.]+)', body, re.IGNORECASE)
        potential_return = ret_match.group(1) if ret_match else ""

        try:
            await page.screenshot(path=screenshot_path)
        except Exception:
            screenshot_path = ""

        if "suspended" in body.lower():
            return PlacementResult(success=False, error="Market suspended", screenshot=screenshot_path)

        if "insufficient" in body.lower():
            return PlacementResult(success=False, error="Insufficient balance", screenshot=screenshot_path)

        # Find Place Bet button
        btn = await page.evaluate("""
            () => {
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.innerText || '').trim();
                    if (t === 'Place Bet' && el.offsetParent) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 30 && r.height > 15)
                            return { found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
                    }
                }
                return { found: false };
            }
        """)

        if not btn.get("found"):
            return PlacementResult(success=False, error="Place Bet button not found", screenshot=screenshot_path)

        await page.mouse.click(btn["x"], btn["y"])
        await asyncio.sleep(DELAY_PLACE_BET)

        body = await page.inner_text("body")

        if any(kw in body.lower() for kw in ["bet placed", "confirmation", "receipt", "bet reference"]):
            logger.info("BET PLACED SUCCESSFULLY")
            return PlacementResult(success=True, odds=betslip_odds, potential_return=potential_return)

        # Handle odds change
        if "accept" in body.lower() and "odds" in body.lower():
            logger.info("Odds changed — accepting")
            accept = await page.evaluate("""
                () => {
                    for (const el of document.querySelectorAll('*')) {
                        const t = (el.innerText || '').trim();
                        if ((t === 'Accept' || t === 'Accept Changes' || t === 'Accept & Place Bet') && el.offsetParent) {
                            const r = el.getBoundingClientRect();
                            return { found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
                        }
                    }
                    return { found: false };
                }
            """)
            if accept.get("found"):
                await page.mouse.click(accept["x"], accept["y"])
                await asyncio.sleep(DELAY_ODDS_CHANGE_ACCEPT)
                body = await page.inner_text("body")
                if any(kw in body.lower() for kw in ["bet placed", "confirmation", "receipt"]):
                    return PlacementResult(success=True, odds="changed", potential_return=potential_return)

        return PlacementResult(success=False, error="Uncertain result", screenshot=screenshot_path)

    async def _clear_betslip(self):
        page = self._page
        for _ in range(3):
            try:
                result = await page.evaluate("""
                    () => {
                        for (const el of document.querySelectorAll('*')) {
                            const t = (el.innerText || '').trim().toLowerCase();
                            const a = (el.getAttribute('aria-label') || '').toLowerCase();
                            if (!el.offsetParent) continue;
                            const r = el.getBoundingClientRect();
                            if ((t === 'remove' || t === 'remove all' || t === '✕' || t === '×' ||
                                 a.includes('remove') || a.includes('delete') || a.includes('close')) &&
                                r.width > 5 && r.width < 200 && r.height > 5) {
                                return { found: true, x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2) };
                            }
                        }
                        return { found: false };
                    }
                """)
                if result.get("found"):
                    await page.mouse.click(result["x"], result["y"])
                    await asyncio.sleep(1)
                else:
                    break
            except Exception:
                break

    @staticmethod
    def _find_player_team(player_name: str, sport: str) -> Optional[str]:
        """Look up a player's team from rosters."""
        from bet365_rosters import find_team
        return find_team(player_name, sport)


# ─── Singleton ───────────────────────────────────────────────────────────────

bet365_service = Bet365Service()
