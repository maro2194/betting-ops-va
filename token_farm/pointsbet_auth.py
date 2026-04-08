"""
PointsBet browser-based auth via patchright (Kasada bypass).

This module handles:
  1. Full browser login (Chrome + Kasada) → captures JWT from auth endpoints
"""
import json
import logging
import time

logger = logging.getLogger("token-farm.pointsbet")

BASE_URL = "https://pointsbet.com.au"
CHROME_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


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


async def pointsbet_browser_login(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Full browser login with Kasada bypass.

    Returns: {success, access_token, refresh_token, customer_id, expires_at}
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
        )
        page = await context.new_page()

        async def handle_response(response):
            nonlocal captured_token
            url = response.url
            is_auth_url = (
                "pointsbet.com" in url
                and any(seg in url for seg in ("/auth/", "/login", "/token"))
                and response.status == 200
            )
            if not is_auth_url:
                return
            try:
                text = await response.text()
                if "accessToken" in text or "access_token" in text:
                    import json as _json
                    data = _json.loads(text)
                    if isinstance(data, dict) and ("accessToken" in data or "access_token" in data):
                        captured_token = data
                        logger.info(f"Captured token from {url}")
            except Exception as e:
                logger.warning(f"Token capture error: {e}")

        page.on("response", handle_response)

        # Navigate to login page and wait for scripts to initialize
        await page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)

        # Fill email
        await page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i]').first.fill(email)
        await page.wait_for_timeout(500)

        # Fill password
        await page.locator('input[type="password"], input[name="password"]').first.fill(password)
        await page.wait_for_timeout(500)

        # Click submit
        await page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("Login")').first.click()

        # Wait for token capture (up to 20 seconds)
        for _ in range(40):
            if captured_token:
                break
            await page.wait_for_timeout(500)

        await browser.close()
        await pw.stop()

        if not captured_token:
            return {"success": False, "error": "Login failed — no token captured"}

        # Normalise camelCase fields
        access_token = captured_token.get("accessToken") or captured_token.get("access_token", "")
        refresh_token = captured_token.get("refreshToken") or captured_token.get("refresh_token")
        customer_id = (
            captured_token.get("crn")
            or captured_token.get("customerId")
            or captured_token.get("customer_id", "")
        )

        # Calculate expires_at from expiresIn (seconds offset) or use raw value
        expires_at = 0
        expires_in = captured_token.get("expiresIn") or captured_token.get("expires_in")
        if expires_in:
            expires_at = int(time.time()) + int(expires_in)
        else:
            expires_at = captured_token.get("expiresAt") or captured_token.get("expires_at", 0)

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "customer_id": str(customer_id),
            "expires_at": expires_at,
        }

    except Exception as e:
        logger.error(f"Browser login failed for {email}: {e}")
        return {"success": False, "error": f"Browser login failed: {e}"}
