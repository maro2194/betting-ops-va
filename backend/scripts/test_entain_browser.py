"""Entain browser login (Patchright + real Chrome).

Usage: python3 test_entain_browser.py <brand> <username> <password>

Captures the full login flow from a real Chrome browser so we can see what
a successful auth actually produces (redirects, cookies, tokens). Saves
results to /tmp/entain_browser_<brand>_<ts>.json for comparison with the
HTTP path.

Exits 0 on login success (access_token captured), 1 otherwise.
"""
import asyncio
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

from patchright.async_api import async_playwright

import os

OXYLABS_USER_PREFIX = "customer-marolete_86olc-cc-au"
OXYLABS_PASSWORD = "K5E=2qcyhfyFZs~"
OXYLABS_HOST = "pr.oxylabs.io"
OXYLABS_PORT = 7777

# G-W mobile AU proxy from VPS DB
GW_MOBILE = {
    "server": "http://geo.g-w.info:10080",
    "username": "user-e2caZKlVb1ERX9yy-type-mobile-session-drxq5zhp-country-AU-rotation-0",
    "password": "TZMcZLh2GXBb4RPg",
}


def build_proxy() -> dict:
    which = os.environ.get("ENTAIN_PROXY", "oxylabs").lower()
    if which == "mobile" or which == "gw":
        return GW_MOBILE
    # Default Oxylabs with fresh sessid
    sess = random.randint(1000000000, 9999999999)
    return {
        "server": f"http://{OXYLABS_HOST}:{OXYLABS_PORT}",
        "username": f"{OXYLABS_USER_PREFIX}-sessid-{sess}-sesstime-10",
        "password": OXYLABS_PASSWORD,
    }


def build_oxylabs_proxy() -> dict:
    return build_proxy()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("entain_browser")


async def run(brand: str, username: str, password: str) -> dict:
    base_url = f"https://www.{brand}.com.au/"
    result: dict = {
        "brand": brand,
        "username": username,
        "started_at": time.time(),
        "events": [],
        "token_responses": [],
        "final_url": "",
        "cookies": [],
        "balance_text": None,
        "success": False,
    }

    def event(msg: str, **extra):
        entry = {"t": time.time(), "msg": msg, **extra}
        result["events"].append(entry)
        log.info(f"{msg} {extra if extra else ''}")

    proxy = build_oxylabs_proxy()
    event("proxy_config", server=proxy["server"], user_prefix=proxy["username"][:50])

    # HEADFUL mode via Xvfb — Kasada detects headless Chrome on auth endpoints
    # (same workaround Token Farm uses for PointsBet). Needs DISPLAY set.
    import os
    headless = os.environ.get("ENTAIN_HEADLESS", "0") == "1"
    event("launch_mode", headless=headless, display=os.environ.get("DISPLAY", "<unset>"))

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        proxy=proxy,
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-AU",
        timezone_id="Australia/Sydney",
        proxy=proxy,
    )
    page = await context.new_page()
    # Note: Patchright handles navigator.webdriver stealth natively.
    # Manual init scripts create detectable artifacts — omit them.

    async def on_resp(resp):
        try:
            url = resp.url
            if resp.status >= 400:
                event("http_error", status=resp.status, url=url[:120])
            if "oauth2/token" in url and resp.status == 200:
                text = await resp.text()
                if "access_token" in text:
                    try:
                        data = json.loads(text)
                        result["token_responses"].append({"url": url, "body": data})
                        event(
                            "access_token_captured",
                            token_len=len(str(data.get("access_token", ""))),
                            keys=list(data.keys()),
                        )
                    except Exception:
                        pass
            if resp.request.method == "POST" and "/auth/" in url:
                event("auth_post_response", status=resp.status, url=url[:120])
        except Exception:
            pass

    page.on("response", on_resp)

    try:
        event("loading_homepage", url=base_url)
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
        event("homepage_loaded", url=page.url[:120])

        # Poll for KPSDK to initialize (Kasada script loads async)
        kpsdk = "undefined"
        for i in range(30):
            await page.wait_for_timeout(1000)
            kpsdk = await page.evaluate("typeof window.KPSDK")
            if kpsdk != "undefined":
                event("kpsdk_ready", sec=i + 1)
                break
        if kpsdk == "undefined":
            # Capture scripts on page to see if Kasada script was even loaded
            scripts = await page.evaluate(
                "() => Array.from(document.querySelectorAll('script[src]')).map(s => s.src)"
            )
            kp_scripts = [s for s in scripts if "149e9513" in s or "kp" in s.lower()]
            event("kpsdk_never_loaded", kp_scripts=kp_scripts, total_scripts=len(scripts))
        event("kpsdk_main", kpsdk=kpsdk)

        # Click login button
        event("searching_login_button")
        clicked = False
        btns = await page.query_selector_all("button, a")
        for btn in btns:
            text = (await btn.text_content() or "").strip().lower()
            if text in ("log in", "login"):
                try:
                    if await btn.is_visible():
                        await btn.click()
                        clicked = True
                        event("login_button_clicked", text=text)
                        break
                except Exception:
                    pass
        if not clicked:
            event("login_button_not_found", WARNING=True)
            # try direct nav
            await page.goto(f"{base_url}auth/login", wait_until="domcontentloaded", timeout=30000)

        await page.wait_for_timeout(10000)
        event("auth_page_reached", url=page.url[:120])

        kpsdk_auth = await page.evaluate("typeof window.KPSDK")
        event("kpsdk_auth", kpsdk=kpsdk_auth)

        # Fill form
        filled = await page.evaluate(
            """([u, p]) => {
                const uf = document.getElementById("username");
                const pf = document.getElementById("password");
                if (uf && pf) {
                    uf.value = u; pf.value = p;
                    uf.dispatchEvent(new Event("input", {bubbles: true}));
                    pf.dispatchEvent(new Event("input", {bubbles: true}));
                    return true;
                }
                return false;
            }""",
            [username, password],
        )
        event("credentials_filled", ok=filled)
        if not filled:
            raise RuntimeError("Could not find username/password fields")

        await page.wait_for_timeout(1500)

        event("submitting")
        try:
            async with page.expect_navigation(timeout=30000, wait_until="domcontentloaded"):
                await page.click("#accept")
        except Exception as e:
            event("navigation_timeout_on_submit", error=str(e))

        await page.wait_for_timeout(15000)
        result["final_url"] = page.url
        event("after_submit", url=page.url[:120])

        logged_in = "/auth/" not in page.url
        event("logged_in_check", logged_in=logged_in)

        if logged_in:
            body = await page.inner_text("body")
            bals = re.findall(r"\$[\d,.]+", body)
            result["balance_text"] = bals[:5]
            event("balance_scan", matches=bals[:5])

        # Dump cookies
        result["cookies"] = await context.cookies()
        event("cookies_captured", count=len(result["cookies"]))

        result["success"] = bool(result["token_responses"]) or logged_in
    finally:
        await browser.close()
        await pw.stop()

    result["finished_at"] = time.time()
    return result


async def main():
    if len(sys.argv) != 4:
        print("Usage: test_entain_browser.py <brand> <username> <password>")
        sys.exit(2)
    brand, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"=== ENTAIN BROWSER TEST === brand={brand} user={username}")
    result = await run(brand, username, password)

    ts = int(time.time())
    out = Path(f"/tmp/entain_browser_{brand}_{ts}.json")
    # Redact cookies to value prefixes to keep the dump readable
    safe = dict(result)
    safe["cookies"] = [
        {"name": c["name"], "domain": c.get("domain"), "value_prefix": str(c.get("value", ""))[:30]}
        for c in result["cookies"]
    ]
    out.write_text(json.dumps(safe, indent=2, default=str))
    print(f"\nDump: {out}")
    print(f"final_url: {result['final_url'][:120]}")
    print(f"token_responses: {len(result['token_responses'])}")
    print(f"cookies: {len(result['cookies'])}")
    print(f"balance_text: {result['balance_text']}")
    print(f"success: {result['success']}")

    if result["token_responses"]:
        tok = result["token_responses"][0]["body"].get("access_token", "")
        print(f"TOKEN captured ({len(tok)} chars): {tok[:40]}...")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
