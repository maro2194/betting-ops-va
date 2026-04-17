"""Neds login - wait for Kasada on main site first, then login."""
import asyncio
import json
import re
import random
from patchright.async_api import async_playwright


async def login():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=True,
        args=["--disable-async-dns", "--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-AU",
        timezone_id="Australia/Sydney",
    )
    page = await context.new_page()
    await page.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')

    token_data = {}

    async def on_resp(resp):
        try:
            url = resp.url
            if "kpsdk" in url.lower() or "kpparam" in url.lower() or "/149e9513" in url:
                print(f"  KP: {resp.status} {url[:80]}")
            if resp.status == 200 and "oauth2/token" in url:
                text = await resp.text()
                if "access_token" in text:
                    token_data["t"] = json.loads(text)
                    print("ACCESS TOKEN CAPTURED!")
            if resp.request.method == "POST" and "auth/login" in url:
                print(f"  LOGIN POST: {resp.status}")
        except:
            pass

    page.on("response", on_resp)

    # Step 1: Load main site and wait for Kasada to FULLY initialize
    for attempt in range(3):
        try:
            print(f"Loading Neds main site (attempt {attempt + 1})...")
            await page.goto("https://www.neds.com.au/", wait_until="domcontentloaded", timeout=20000)
            print("Loaded!")
            break
        except:
            if attempt == 2:
                await browser.close()
                await pw.stop()
                return
            await asyncio.sleep(2)

    # Wait LONG for Kasada to initialize on main site
    print("Waiting 15s for Kasada to fully initialize...")
    await page.wait_for_timeout(15000)

    # Check Kasada status on main site
    kp_main = await page.evaluate("typeof window.KPSDK")
    print(f"KPSDK on main site: {kp_main}")

    # Check cookies for Kasada tokens
    cookies = await context.cookies()
    kp_cookies = [c for c in cookies if "kp" in c["name"].lower() or "kasada" in c["name"].lower() or "_ct" in c["name"]]
    print(f"KP cookies: {[c['name'] for c in kp_cookies]}")
    all_cookie_names = [c["name"] for c in cookies]
    print(f"All cookies: {all_cookie_names}")

    # Step 2: Click login
    print("\nClicking login...")
    btns = await page.query_selector_all("button, a")
    for btn in btns:
        text = (await btn.text_content() or "").strip().lower()
        if text in ("log in", "login"):
            if await btn.is_visible():
                await btn.click()
                break

    # Wait for auth page + Kasada reinit
    print("Waiting 15s for auth page + Kasada...")
    await page.wait_for_timeout(15000)
    print(f"Auth page: {page.url[:100]}")

    kp_auth = await page.evaluate("typeof window.KPSDK")
    on_submit = await page.evaluate("typeof onSubmit")
    print(f"KPSDK on auth: {kp_auth}, onSubmit: {on_submit}")

    # Check all scripts for Kasada
    scripts = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('script[src]')).map(s => s.src);
    }""")
    kp_scripts = [s for s in scripts if "kp" in s.lower() or "149e9513" in s]
    print(f"KP script tags: {kp_scripts}")

    # Fill and submit regardless
    await page.evaluate('''() => {
        const u = document.getElementById("username");
        const p = document.getElementById("password");
        if (u && p) {
            u.value = "williamdean327";
            p.value = "Deanslister27!";
            u.dispatchEvent(new Event("input", {bubbles: true}));
            p.dispatchEvent(new Event("input", {bubbles: true}));
        }
    }''')
    print("Filled credentials")
    await page.wait_for_timeout(1000)

    print("Submitting...")
    try:
        async with page.expect_navigation(timeout=30000, wait_until="domcontentloaded"):
            await page.click("#accept")
    except:
        pass

    await page.wait_for_timeout(15000)
    print(f"\nFinal URL: {page.url[:120]}")

    if "/auth/" not in page.url:
        print("\n=== LOGGED IN! ===")
        body = await page.inner_text("body")
        bals = re.findall(r'\$[\d,.]+', body)
        print(f"Balances: {bals[:5]}")
        if token_data:
            t = token_data["t"]
            at = str(t.get("access_token", ""))
            print(f"access_token ({len(at)} chars): {at[:80]}...")
    else:
        print("Still on auth page")

    await browser.close()
    await pw.stop()


asyncio.run(login())
