"""
Run LOCALLY — captures WebSocket messages from Sportsbet to find player prop data.
Opens the match page, waits for Ably to push granular player lines.
"""
import json
import os
import sys
import time

SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

PROXY = "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
EVENT_URL = "https://www.sportsbet.com.au/betting/australian-rules/afl/brisbane-lions-v-collingwood/10294344"

ws_messages = []
api_calls = []

def main():
    from patchright.sync_api import sync_playwright
    from sportsbet_cli.client import SportsbetClient

    client = SportsbetClient(proxy=PROXY)
    local_port = client._start_proxy_forwarder(PROXY)

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        channel="chrome",
        headless=False,
        args=[
            f"--proxy-server=127.0.0.1:{local_port}",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    page = browser.new_page()

    # Capture ALL responses (REST + websocket frames won't show here but the Ably REST fallback will)
    def on_response(response):
        url = response.url
        if "/apigw/" in url or "ably" in url.lower():
            try:
                body = response.body()
                size = len(body)
            except:
                size = 0
            if size > 50:
                api_calls.append({"url": url[:200], "status": response.status, "size": size})
                print(f"  API: {response.status} {size:>8d}b {url[:120]}")

    # Capture WebSocket frames
    def on_ws(ws):
        print(f"\n  WS opened: {ws.url[:100]}")
        def on_frame(payload):
            ws_messages.append({"url": ws.url[:100], "data": payload[:500] if isinstance(payload, str) else str(payload)[:500]})
            if len(ws_messages) % 10 == 0:
                print(f"  WS frames: {len(ws_messages)}")
        ws.on("framereceived", lambda payload: on_frame(payload))

    page.on("response", on_response)
    page.on("websocket", on_ws)

    print(f"Loading {EVENT_URL}...")
    page.goto(EVENT_URL, wait_until="domcontentloaded", timeout=30000)

    print("Waiting 15s for Ably data...")
    time.sleep(15)

    print(f"\nWS messages so far: {len(ws_messages)}")
    print(f"API calls so far: {len(api_calls)}")

    # Try expanding player props by evaluating JS
    print("\nExpanding disposal markets...")
    page.evaluate("""
        // Click on any "Disposals" or player prop tabs
        const tabs = document.querySelectorAll('[data-automation-id]');
        for (const tab of tabs) {
            const text = tab.textContent || '';
            if (text.includes('Disposals') || text.includes('Player Props') || text.includes('Stats')) {
                tab.click();
                break;
            }
        }
    """)
    time.sleep(10)

    print(f"\nAfter expansion: WS messages={len(ws_messages)}, API calls={len(api_calls)}")

    # Save everything
    with open("sb_ws_messages.json", "w") as f:
        json.dump(ws_messages[:100], f, indent=2)  # First 100 messages
    with open("sb_api_calls2.json", "w") as f:
        json.dump(api_calls, f, indent=2)

    # Analyze WS messages for market data
    print(f"\n=== WS Message Analysis ===")
    for msg in ws_messages[:5]:
        data = msg["data"]
        if "disposal" in data.lower() or "market" in data.lower() or "outcome" in data.lower():
            print(f"  Relevant: {data[:200]}")

    # Check if any API call has the granular data
    for call in api_calls:
        if call["size"] > 10000:
            print(f"\n  Large API: {call['url']} ({call['size']} bytes)")

    browser.close()
    pw.stop()

if __name__ == "__main__":
    main()
