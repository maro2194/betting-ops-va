"""Entain login via Camoufox (Firefox-based, anti-detect).

Kasada reliably detects Patchright/Chrome-headless. Camoufox is the same
tool already used successfully against bet365 (also Kasada-protected).

Usage: python3 test_entain_camoufox.py <brand> <username> <password>
Env:   ENTAIN_PROXY=oxylabs|mobile  (default oxylabs)
"""
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("entain_camoufox")

OXYLABS_USER_PREFIX = "customer-marolete_86olc-cc-au"
OXYLABS_PASSWORD = "K5E=2qcyhfyFZs~"

GW_MOBILE = {
    "server": "http://geo.g-w.info:10080",
    "username": "user-e2caZKlVb1ERX9yy-type-mobile-session-drxq5zhp-country-AU-rotation-0",
    "password": "TZMcZLh2GXBb4RPg",
}

# IPRoyal rotating AU — known working for bet365 (Kasada)
IPROYAL = {
    "server": "http://geo.iproyal.com:12321",
    "username": "FuTUVMvrSTa9cYM8",
    "password": "XZbc7POb6z75bzCb_country-au",
}


def build_proxy() -> dict:
    which = os.environ.get("ENTAIN_PROXY", "oxylabs").lower()
    if which in ("mobile", "gw"):
        return GW_MOBILE
    if which in ("iproyal", "ipr"):
        return IPROYAL
    sess = random.randint(1000000000, 9999999999)
    return {
        "server": "http://pr.oxylabs.io:7777",
        "username": f"{OXYLABS_USER_PREFIX}-sessid-{sess}-sesstime-10",
        "password": OXYLABS_PASSWORD,
    }


async def run(brand: str, username: str, password: str) -> dict:
    base_url = f"https://www.{brand}.com.au/"
    result: dict = {
        "brand": brand,
        "username": username,
        "proxy": None,
        "events": [],
        "token_responses": [],
        "final_url": "",
        "cookies": [],
        "success": False,
    }

    def event(msg: str, **extra):
        entry = {"t": time.time(), "msg": msg, **extra}
        result["events"].append(entry)
        log.info(f"{msg} {extra if extra else ''}")

    from camoufox.async_api import AsyncCamoufox

    proxy = build_proxy()
    result["proxy"] = {k: v for k, v in proxy.items() if k != "password"}
    event("proxy_config", server=proxy["server"])

    cfox_kwargs = {"headless": True, "proxy": proxy, "geoip": True}

    browser = None
    try:
        browser = await AsyncCamoufox(**cfox_kwargs).__aenter__()
        page = await browser.new_page()

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
                            )
                        except Exception:
                            pass
                if resp.request.method == "POST" and "/auth/" in url:
                    event("auth_post_response", status=resp.status, url=url[:120])
            except Exception:
                pass

        page.on("response", on_resp)

        event("loading_homepage", url=base_url)
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
        event("homepage_loaded", url=page.url[:120])

        # KPSDK init poll
        kpsdk = "undefined"
        for i in range(20):
            await page.wait_for_timeout(1000)
            kpsdk = await page.evaluate("typeof window.KPSDK")
            if kpsdk != "undefined":
                event("kpsdk_ready", sec=i + 1)
                break
        event("kpsdk_main", kpsdk=kpsdk)

        # Click login button (search buttons + anchors)
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
            event("login_button_fallback_nav")
            await page.goto(f"{base_url}auth/login", wait_until="domcontentloaded", timeout=30000)

        # Wait for auth page
        await page.wait_for_timeout(8000)
        event("auth_page_reached", url=page.url[:120])

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
            event("navigation_timeout_on_submit", error=str(e)[:200])

        await page.wait_for_timeout(12000)
        result["final_url"] = page.url
        event("after_submit", url=page.url[:120])
        logged_in = "/auth/" not in page.url
        event("logged_in_check", logged_in=logged_in)

        if logged_in:
            body = await page.inner_text("body")
            bals = re.findall(r"\$[\d,.]+", body)
            result["balance_text"] = bals[:5]

        result["cookies"] = await page.context.cookies()
        result["success"] = bool(result["token_responses"]) or logged_in
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    return result


async def main():
    if len(sys.argv) != 4:
        print("Usage: test_entain_camoufox.py <brand> <username> <password>")
        sys.exit(2)
    brand, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"=== ENTAIN CAMOUFOX TEST === brand={brand} user={username}")
    result = await run(brand, username, password)

    ts = int(time.time())
    out = Path(f"/tmp/entain_camoufox_{brand}_{ts}.json")
    safe = dict(result)
    safe["cookies"] = [
        {"name": c.get("name"), "domain": c.get("domain"),
         "value_prefix": str(c.get("value", ""))[:30]}
        for c in result.get("cookies", [])
    ]
    out.write_text(json.dumps(safe, indent=2, default=str))
    print(f"\nDump: {out}")
    print(f"final_url: {result['final_url'][:120]}")
    print(f"token_responses: {len(result['token_responses'])}")
    print(f"cookies: {len(result['cookies'])}")
    print(f"success: {result['success']}")

    if result["token_responses"]:
        tok = result["token_responses"][0]["body"].get("access_token", "")
        print(f"TOKEN captured ({len(tok)} chars): {tok[:40]}...")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
