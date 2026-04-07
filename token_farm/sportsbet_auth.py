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


async def sportsbet_get_promos(email: str, password: str, proxy_url: str | None = None) -> dict:
    """Fetch Sportsbet promotions via browser login + API interception.

    Logs in via patchright, then intercepts promo API responses that SB's
    frontend auto-fetches (vouchers, freebets, preferred-promotions).
    This ensures Kasada cookies are present for all API calls.

    Returns unified promo structure:
      {success, boost_tokens, bonus_back_tokens, deposit_match_tokens, promos, redeemed}
    """
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "patchright not installed",
                "boost_tokens": 0, "bonus_back_tokens": 0, "deposit_match_tokens": 0, "promos": [], "redeemed": []}

    proxy = _parse_proxy(proxy_url)
    voucher_data = None
    freebet_data = None
    promo_data = None

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled"],
            proxy=proxy,
        )
        context = await browser.new_context(
            user_agent=CHROME_UA,
            viewport={"width": 1920, "height": 1080},
            proxy=proxy,
        )
        page = await context.new_page()

        # Intercept promo-related responses
        async def handle_response(response):
            nonlocal voucher_data, freebet_data, promo_data
            url = response.url
            try:
                if "/vouchers/customer/voucher" in url and response.status == 200:
                    text = await response.text()
                    import json as _json
                    voucher_data = _json.loads(text)
                    logger.info(f"Captured vouchers: {len(voucher_data.get('vouchers', []))} items")
                elif "/accounts/freebets" in url and response.status == 200:
                    text = await response.text()
                    import json as _json
                    freebet_data = _json.loads(text)
                elif "/preferred-promotions/" in url and "/customers/" in url and response.status == 200:
                    text = await response.text()
                    import json as _json
                    promo_data = _json.loads(text)
                    logger.info(f"Captured preferred promos: {len(promo_data.get('promotions', []))} items")
            except Exception:
                pass

        page.on("response", handle_response)

        # Navigate and login
        await page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(10000)

        login_btn = page.locator('[data-automation-id="header-login-touchable"]')
        await login_btn.wait_for(state="visible", timeout=15000)
        await login_btn.click(timeout=15000)
        await page.wait_for_timeout(2000)

        await page.locator('[data-automation-id="login-username"]').fill(email)
        await page.wait_for_timeout(500)
        await page.locator('[data-automation-id="login-password"]').fill(password)
        await page.wait_for_timeout(500)
        await page.locator('button[type="submit"]').click()

        # Wait for login to complete
        await page.wait_for_timeout(12000)

        # Navigate to trigger promo loading — SB loads vouchers on page transitions
        try:
            await page.goto(f"{BASE_URL}/promotions", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(8000)
        except Exception:
            pass  # promotions page might redirect, that's ok

        # Also try account page which triggers freebet loading
        try:
            await page.goto(f"{BASE_URL}/account/my-offers", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)
        except Exception:
            pass

        logger.info(f"Promo capture: vouchers={'YES' if voucher_data else 'NO'} freebets={'YES' if freebet_data else 'NO'} promos={'YES' if promo_data else 'NO'}")

        await browser.close()
        await pw.stop()

    except Exception as e:
        logger.error(f"sportsbet_get_promos browser error: {e}")
        return {"success": False, "error": str(e),
                "boost_tokens": 0, "bonus_back_tokens": 0, "deposit_match_tokens": 0, "promos": [], "redeemed": []}

    # Parse captured data into unified format
    boost_tokens = 0
    bonus_back_tokens = 0
    deposit_match_tokens = 0
    promos: list[dict] = []

    # 1. Vouchers — Power Plays
    if voucher_data:
        vouchers = voucher_data if isinstance(voucher_data, list) else voucher_data.get("vouchers", [])
        for v in (vouchers if isinstance(vouchers, list) else []):
            vtype = v.get("type", v.get("voucherType", "POWERPLAY"))
            desc = v.get("description", v.get("name", vtype))
            expiry = v.get("absoluteExpiryDate", v.get("expiryDate", v.get("expiry", "")))
            if isinstance(expiry, (int, float)) and expiry > 0:
                import datetime
                expiry = datetime.datetime.utcfromtimestamp(expiry).isoformat()
            status_raw = v.get("status", "ACTIVE")
            boost_tokens += 1
            promos.append({
                "promo_id": str(v.get("id", v.get("voucherId", v.get("rewardId", "")))),
                "type": "Boost",
                "description": desc,
                "status": "Active" if status_raw == "ACTIVE" else status_raw.capitalize(),
                "tokens": 1, "tokens_remaining": 1,
                "expiry_date": str(expiry),
                "boost_data": {"boost_type": vtype, "boost_percentage": 0},
                "bonus_back_data": None, "deposit_match_data": None,
            })

    # 2. Freebets
    if freebet_data:
        fb_list = freebet_data if isinstance(freebet_data, list) else freebet_data.get("freebetTokens", freebet_data.get("freebets", []))
        for fb in (fb_list if isinstance(fb_list, list) else []):
            amount = float(fb.get("amount", fb.get("value", fb.get("tokenValue", 0))))
            if amount <= 0:
                continue
            expiry = fb.get("expiryDate", fb.get("expiry", ""))
            bonus_back_tokens += 1
            promos.append({
                "promo_id": str(fb.get("freebetTokenId", fb.get("id", ""))),
                "type": "BonusBack",
                "description": f"${amount:.2f} Bonus Bet",
                "status": "Active", "tokens": 1, "tokens_remaining": 1,
                "expiry_date": str(expiry),
                "boost_data": None,
                "bonus_back_data": {
                    "details": f"Bonus bet worth ${amount:.2f}",
                    "criteria": fb.get("freebetTokenType", "SPORTS"),
                    "event_config_type": "Global", "global_config": [], "event_config": [],
                    "max_deposit": int(amount * 100), "field_size": 0,
                },
                "deposit_match_data": None,
            })

    # 3. Preferred promotions — Second Chance SGM, racing promos
    if promo_data:
        plist = promo_data if isinstance(promo_data, list) else promo_data.get("promotions", [])
        for p in (plist if isinstance(plist, list) else []):
            title = p.get("title", p.get("name", p.get("displayName", "Promotion")))
            promo_id = str(p.get("promotionId", p.get("id", p.get("offerId", ""))))
            # Extract dollar value from various fields
            value = 0.0
            for vk in ["value", "amount", "tokenValue", "maxPayout", "bonusAmount"]:
                try:
                    v = float(p.get(vk, 0))
                    if v > 0:
                        value = v
                        break
                except (TypeError, ValueError):
                    pass
            # Also check description/title for dollar amounts
            if value == 0:
                import re
                m = re.search(r'\$(\d+(?:\.\d{2})?)', str(title) + str(p.get("description", "")))
                if m:
                    value = float(m.group(1))

            desc = title
            if value > 0:
                desc += f" (${value:.0f})"

            expiry = p.get("expiryDate", p.get("endDate", p.get("absoluteExpiryDate", "")))
            if isinstance(expiry, (int, float)) and expiry > 0:
                import datetime
                expiry = datetime.datetime.utcfromtimestamp(expiry).isoformat()

            # Categorize: Second Chance / racing promos → BonusBack
            bonus_back_tokens += 1
            promos.append({
                "promo_id": promo_id,
                "type": "BonusBack",
                "description": desc,
                "status": "Active", "tokens": 1, "tokens_remaining": 1,
                "expiry_date": str(expiry),
                "boost_data": None,
                "bonus_back_data": {
                    "details": desc,
                    "criteria": p.get("type", p.get("promotionType", p.get("cardType", "Promotion"))),
                    "event_config_type": "Global", "global_config": [], "event_config": [],
                    "max_deposit": int(value * 100), "field_size": 0,
                },
                "deposit_match_data": None,
            })

    return {
        "success": True,
        "boost_tokens": boost_tokens,
        "bonus_back_tokens": bonus_back_tokens,
        "deposit_match_tokens": deposit_match_tokens,
        "promos": promos,
        "redeemed": [],
    }


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
