"""Entain login via Patchright (Chrome) from Token Farm mini PC.

Patterns copied from working bet365_auth.py:
  - channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"]
  - CHROME_UA Chrome/136
  - No init_script (Patchright handles stealth natively)
  - Cookie consent click + multiple retries for login button
  - Human-like waits

Usage: python3 test_entain_patchright_tf.py <brand> <username> <password>
"""
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("entain_pr_tf")

CHROME_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"
)


async def run(brand: str, username: str, password: str) -> dict:
    base_url = f"https://www.{brand}.com.au/"
    result = {
        "brand": brand, "username": username, "events": [],
        "token_responses": [], "final_url": "", "balance_text": None,
        "success": False,
    }

    def event(msg, **extra):
        entry = {"t": time.time(), "msg": msg, **extra}
        result["events"].append(entry)
        log.info(f"{msg} {extra if extra else ''}")

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent=CHROME_UA,
        viewport={"width": 1920, "height": 1080},
        locale="en-AU",
        timezone_id="Australia/Sydney",
    )
    page = await context.new_page()

    async def on_resp(resp):
        try:
            if "oauth2/token" in resp.url and resp.status == 200:
                text = await resp.text()
                if "access_token" in text:
                    data = json.loads(text)
                    result["token_responses"].append({"url": resp.url, "body": data})
                    event("access_token_captured",
                          token_len=len(str(data.get("access_token", ""))))
        except Exception:
            pass
    page.on("response", on_resp)

    try:
        event("loading_homepage", url=base_url)
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
        event("homepage_loaded", url=page.url[:120])

        # Let Kasada + SPA init — generous wait like bet365
        await page.wait_for_timeout(8000)

        # Handle cookie consent (Entain sites show it)
        for cs in [
            page.get_by_text("Accept All", exact=True),
            page.get_by_text("Accept", exact=True),
            page.locator("button[class*='onetrust']"),
            page.locator("#onetrust-accept-btn-handler"),
        ]:
            try:
                if await cs.count() > 0:
                    await cs.first.click(timeout=5000)
                    event("cookie_banner_accepted")
                    await page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        # Click Login — retry 3x with waits (bet365 pattern)
        clicked = False
        for attempt in range(3):
            for sel in [
                page.get_by_text("Log in", exact=True),
                page.get_by_text("Login", exact=True),
                page.locator("button:has-text('Log in')"),
                page.locator("a:has-text('Log in')"),
                page.locator("[data-testid*='login']"),
            ]:
                try:
                    count = await sel.count()
                    for i in range(min(count, 5)):
                        if await sel.nth(i).is_visible():
                            await sel.nth(i).click(timeout=5000)
                            clicked = True
                            event("login_button_clicked", attempt=attempt, selector_idx=i)
                            break
                except Exception:
                    continue
                if clicked:
                    break
            if clicked:
                break
            event("login_button_retry", attempt=attempt + 1)
            await page.wait_for_timeout(3000)

        if not clicked:
            # Try direct nav as last resort
            event("login_button_not_found_direct_nav")
            # build minimal oauth2 auth URL
            client_id = {
                "ladbrokes": "4f075c04-8e6b-4e67-9c00-37fe66415ff1",
                "neds": "89362562-8df2-425c-8bf0-b7b490ba145d",
            }[brand]
            import secrets
            state = secrets.token_urlsafe(32)
            auth_url = (
                f"{base_url}api/providers/auth/oauth2/auth"
                f"?client_id={client_id}&response_type=code"
                f"&redirect_uri={base_url}callback&scope=openid&state={state}"
            )
            await page.goto(auth_url, wait_until="domcontentloaded", timeout=30000)

        # Wait for auth/login form
        await page.wait_for_timeout(6000)
        event("auth_page_reached", url=page.url[:120])

        # Fill form using selector + click
        u_sel = page.locator("#username")
        p_sel = page.locator("#password")
        await u_sel.wait_for(state="visible", timeout=15000)
        await u_sel.fill(username)
        await p_sel.fill(password)
        event("credentials_filled")

        # Small human-like pause before submit
        await page.wait_for_timeout(1500)

        event("submitting")
        try:
            async with page.expect_navigation(timeout=45000, wait_until="domcontentloaded"):
                await page.click("#accept")
        except Exception as e:
            event("nav_timeout", error=str(e)[:200])

        await page.wait_for_timeout(12000)
        result["final_url"] = page.url
        event("after_submit", url=page.url[:120])

        logged_in = "/auth/" not in page.url
        event("logged_in_check", logged_in=logged_in)
        if logged_in:
            body = await page.inner_text("body")
            bals = re.findall(r"\$[\d,.]+", body)
            result["balance_text"] = bals[:5]
            event("balance_scan", matches=bals[:5])

        result["success"] = bool(result["token_responses"]) or logged_in
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass
    return result


async def main():
    if len(sys.argv) != 4:
        print("Usage: test_entain_patchright_tf.py <brand> <username> <password>")
        sys.exit(2)
    brand, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"=== ENTAIN Patchright (TF) === brand={brand} user={username}")
    result = await run(brand, username, password)

    print(f"\nfinal_url: {result['final_url'][:120]}")
    print(f"token_responses: {len(result['token_responses'])}")
    print(f"balance_text: {result['balance_text']}")
    print(f"success: {result['success']}")
    if result["token_responses"]:
        tok = result["token_responses"][0]["body"].get("access_token", "")
        print(f"TOKEN ({len(tok)} chars): {tok[:40]}...")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
