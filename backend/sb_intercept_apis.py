"""
Run LOCALLY — intercepts all API calls when loading a Sportsbet match page.
Captures the endpoints that load granular player props (10+, 11+, 12+ disposals etc.)
"""
import json
import os
import sys
import time

SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

from patchright.sync_api import sync_playwright

PROXY = "http://user001:pizza33@193.30.101.8:12323"
EVENT_URL = "https://www.sportsbet.com.au/betting/australian-rules/afl/brisbane-lions-v-collingwood/10294344"

api_calls = []

def main():
    pw = sync_playwright().start()

    # Use sportsbet-cli's proxy forwarder approach
    from sportsbet_cli.client import SportsbetClient
    client = SportsbetClient(proxy=PROXY)
    local_port = client._start_proxy_forwarder(PROXY)

    browser = pw.chromium.launch(
        channel="chrome",
        headless=False,
        args=[
            f"--proxy-server=127.0.0.1:{local_port}",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    page = browser.new_page()

    def on_response(response):
        url = response.url
        # Capture all /apigw/ and WebSocket-related calls
        if "/apigw/" in url or "ably" in url or "sportsbook" in url:
            status = response.status
            size = len(response.body()) if status == 200 else 0
            api_calls.append({
                "url": url[:200],
                "status": status,
                "size": size,
                "method": response.request.method,
            })
            if "/apigw/" in url and status == 200 and size > 100:
                print(f"  API: {response.request.method} {url[:120]} ({size} bytes)")

    page.on("response", on_response)

    print(f"Loading {EVENT_URL}...")
    page.goto(EVENT_URL, wait_until="domcontentloaded", timeout=30000)

    print("Waiting 10s for initial load...")
    time.sleep(10)

    # Click on "Disposals" tab or expand player props
    print("Looking for Disposals tab...")
    for text in ["Disposals", "Player Props", "Stats"]:
        try:
            el = page.locator(f'text="{text}"').first
            if el.is_visible(timeout=3000):
                el.click()
                print(f"  Clicked '{text}'")
                time.sleep(5)
                break
        except:
            continue

    # Click on a player to expand their lines
    print("Looking for player to expand...")
    for player in ["Nick Daicos", "Lachie Neale", "Will Ashcroft"]:
        try:
            el = page.locator(f'text="{player}"').first
            if el.is_visible(timeout=3000):
                el.click()
                print(f"  Clicked '{player}'")
                time.sleep(5)
                break
        except:
            continue

    print(f"\nWaiting 10s for all data to load...")
    time.sleep(10)

    # Save all captured API calls
    print(f"\n=== {len(api_calls)} API calls captured ===")
    for call in api_calls:
        if call["status"] == 200 and call["size"] > 0:
            print(f"  {call['method']:4s} {call['status']} {call['size']:>8d}b {call['url']}")

    with open("sb_api_calls.json", "w") as f:
        json.dump(api_calls, f, indent=2)
    print(f"\nSaved to sb_api_calls.json")

    browser.close()
    pw.stop()

if __name__ == "__main__":
    main()
