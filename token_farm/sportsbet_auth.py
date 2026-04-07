"""
Sportsbet browser-based auth via patchright (Kasada bypass).

This module handles:
  1. Full browser login (Chrome + Kasada) → captures JWT from /ciam/token
  2. Token refresh via API (no browser needed)
"""
import base64
import json
import logging
import re

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("token-farm.sportsbet")

BASE_URL = "https://www.sportsbet.com.au"
CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"


def _decode_jwt(jwt_token: str) -> dict:
    parts = jwt_token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT")
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


def _parse_proxy(proxy_url: str | None) -> dict | None:
    """Parse proxy URL into patchright proxy config.
    Supports: http://user:pass@host:port or http://host:port
    Handles URL-encoded special chars in passwords.
    """
    if not proxy_url:
        return None
    from urllib.parse import urlparse, unquote
    p = urlparse(proxy_url)
    proxy = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        proxy["username"] = unquote(p.username)
    if p.password:
        proxy["password"] = unquote(p.password)
    return proxy


async def sportsbet_browser_login(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Full browser login with Kasada bypass.

    Returns: {success, access_token, refresh_token, customer_id, account_number, expires_at}
    """
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "patchright not installed"}

    captured_token = None
    proxy = _parse_proxy(proxy_url)

    try:
        pw = await async_playwright().start()

        browser = await pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            proxy=proxy,
        )
        context = await browser.new_context(
            user_agent=CHROME_UA,
            viewport={"width": 1920, "height": 1080},
            proxy=proxy,
        )
        page = await context.new_page()

        async def handle_response(response):
            nonlocal captured_token
            if "/ciam/token" in response.url and response.status == 200:
                try:
                    text = await response.text()
                    if "access_token" in text:
                        import json as _json
                        captured_token = _json.loads(text)
                        logger.info(f"Captured token from {response.url}")
                except Exception as e:
                    logger.warning(f"Token capture error: {e}")

        page.on("response", handle_response)

        # Navigate and wait for Kasada scripts to fully initialize
        await page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)

        # Open login modal
        login_btn = page.locator('[data-automation-id="header-login-touchable"]')
        await login_btn.wait_for(state="visible", timeout=15000)
        await login_btn.click(timeout=15000)
        await page.wait_for_timeout(2000)

        # Fill credentials
        await page.locator('[data-automation-id="login-username"]').fill(email)
        await page.wait_for_timeout(500)
        await page.locator('[data-automation-id="login-password"]').fill(password)
        await page.wait_for_timeout(500)
        await page.locator('button[type="submit"]').click()

        # Wait for token capture (up to 15 seconds)
        for _ in range(30):
            if captured_token:
                break
            await page.wait_for_timeout(500)

        await browser.close()
        await pw.stop()

        if not captured_token:
            return {"success": False, "error": "Login failed — no token captured"}

        claims = _decode_jwt(captured_token["access_token"])
        return {
            "success": True,
            "access_token": captured_token["access_token"],
            "refresh_token": captured_token.get("refresh_token"),
            "customer_id": str(claims.get("custId", "")),
            "account_number": str(claims.get("accountNo", "")),
            "username": claims.get("username") or claims.get("email", email),
            "expires_at": claims.get("exp", 0),
        }

    except Exception as e:
        logger.error(f"Browser login failed for {email}: {e}")
        return {"success": False, "error": f"Browser login failed: {e}"}


async def sportsbet_token_refresh(refresh_token: str, proxy_url: str | None = None) -> dict:
    """Refresh JWT via API (no browser needed)."""
    try:
        url = f"{BASE_URL}/apigw/ciam/token"
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/",
            "user-agent": CHROME_UA,
        }
        body = f"grant_type=refresh_token&refresh_token={refresh_token}"

        async with AsyncSession(impersonate="chrome131", proxy=proxy_url) as s:
            resp = await s.post(url, headers=headers, data=body, timeout=15)

        if resp.status_code != 200:
            return {"success": False, "error": f"Refresh HTTP {resp.status_code}"}

        data = resp.json()
        if "access_token" not in data:
            return {"success": False, "error": "No access_token in response"}

        claims = _decode_jwt(data["access_token"])
        return {
            "success": True,
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", refresh_token),
            "customer_id": str(claims.get("custId", "")),
            "account_number": str(claims.get("accountNo", "")),
            "expires_at": claims.get("exp", 0),
        }

    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return {"success": False, "error": f"Refresh error: {e}"}
