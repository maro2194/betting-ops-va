"""
Crack Sportsbet's Ably WebSocket to get granular player lines.
Run LOCALLY — opens Chrome, navigates to match, clicks player to trigger Ably subscription,
captures the WebSocket frames via CDP Network domain.
"""
import json
import os
import sys
import time
import subprocess
import shutil
import urllib.request

SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

PROXY = "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
EVENT_URL = "https://www.sportsbet.com.au/betting/australian-rules/afl/brisbane-lions-v-collingwood/10294344"
CDP_PORT = 9224

ws_frames = []

def main():
    from sportsbet_cli.client import SportsbetClient
    client = SportsbetClient(proxy=PROXY)
    local_port = client._start_proxy_forwarder(PROXY)

    prof = os.path.join(os.environ.get("TEMP", "/tmp"), "sb_ably_prof")
    if os.path.exists(prof):
        shutil.rmtree(prof, ignore_errors=True)

    chrome = shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 6

    proc = subprocess.Popen([
        chrome, f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run", "--no-default-browser-check",
        f"--user-data-dir={prof}", "--window-size=1200,900",
        f"--proxy-server=127.0.0.1:{local_port}",
        "--disable-blink-features=AutomationControlled",
        "about:blank",
    ], startupinfo=si, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(10):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
            print("CDP ready")
            break
        except:
            pass

    from patchright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    page = browser.contexts[0].pages[0]

    # Enable CDP Network domain to capture WebSocket frames
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")

    ws_connections = {}

    def on_ws_created(params):
        ws_id = params.get("requestId", "")
        url = params.get("url", "")
        ws_connections[ws_id] = url
        if "ably" in url.lower() or "realtime" in url.lower():
            print(f"  WS created: {url[:100]}")

    def on_ws_frame(params):
        ws_id = params.get("requestId", "")
        url = ws_connections.get(ws_id, "")
        payload = params.get("response", {}).get("payloadData", "")
        if payload and len(payload) > 20:
            ws_frames.append({"url": url[:80], "data": payload[:2000], "size": len(payload)})
            # Check for market/disposal data
            if "dispos" in payload.lower() or "market" in payload.lower() or "outcome" in payload.lower():
                print(f"  WS MARKET DATA: {len(payload)} bytes from {url[:50]}")

    cdp.on("Network.webSocketCreated", on_ws_created)
    cdp.on("Network.webSocketFrameReceived", on_ws_frame)

    print(f"\nLoading {EVENT_URL}...")
    page.goto(EVENT_URL, wait_until="domcontentloaded", timeout=30000)
    print("Waiting 15s for initial load + Ably...")
    time.sleep(15)
    print(f"WS frames so far: {len(ws_frames)}")

    # Now click to expand player disposal lines
    print("\nExpanding player props...")
    # Click "Disposals" tab if visible
    try:
        page.evaluate("""
            const els = document.querySelectorAll('*');
            for (const el of els) {
                const text = (el.textContent || '').trim();
                if ((text === 'Disposals' || text === 'Player Disposals') && el.offsetParent) {
                    el.click();
                    break;
                }
            }
        """)
        time.sleep(5)
    except:
        pass

    # Click on individual players to trigger line data
    for player in ["Nick Daicos", "Lachie Neale", "Will Ashcroft", "Harry Perryman"]:
        try:
            el = page.locator(f'text="{player}"').first
            if el.is_visible(timeout=2000):
                el.click()
                print(f"  Clicked {player}")
                time.sleep(3)
        except:
            pass

    print(f"\nWaiting 10s for data...")
    time.sleep(10)

    print(f"\n=== Results ===")
    print(f"WS frames: {len(ws_frames)}")
    print(f"WS connections: {list(ws_connections.values())}")

    # Analyze frames
    market_frames = [f for f in ws_frames if "market" in f["data"].lower() or "dispos" in f["data"].lower() or "outcome" in f["data"].lower()]
    print(f"Market-related frames: {len(market_frames)}")

    for f in market_frames[:5]:
        print(f"\n  Size: {f['size']} bytes, URL: {f['url']}")
        # Try to parse as JSON
        data = f["data"]
        try:
            parsed = json.loads(data)
            print(f"  JSON: {json.dumps(parsed, indent=2)[:500]}")
        except:
            print(f"  Raw: {data[:300]}")

    # Save all frames
    with open("sb_ably_frames.json", "w") as fp:
        json.dump(ws_frames, fp, indent=2)
    print(f"\nSaved {len(ws_frames)} frames to sb_ably_frames.json")

    # Also extract the full Redux state AFTER clicking
    state = page.evaluate("""
        () => {
            if (window.__STORE__) return JSON.parse(JSON.stringify(window.__STORE__.getState()));
            return null;
        }
    """)
    if state:
        sb = state.get("entities", {}).get("sportsbook", {})
        markets = sb.get("markets", {})
        outcomes = sb.get("outcomes", {})
        print(f"\nRedux after clicks: {len(markets)} markets, {len(outcomes)} outcomes")

        # Check for NEW markets (player lines)
        EVENT_ID = "10294344"
        ev_mkts = {m.get("name"): len(m.get("outcomeIds", [])) for m in markets.values() if str(m.get("eventId")) == EVENT_ID}
        print(f"Event markets: {len(ev_mkts)}")
        for name in sorted(ev_mkts.keys()):
            cnt = ev_mkts[name]
            if "dispos" in name.lower() or "+" in name:
                print(f"  ** {name}: {cnt}")
            else:
                print(f"     {name}: {cnt}")

        with open("sb_state_after_clicks.json", "w") as fp:
            json.dump(sb, fp)
        print("Saved state to sb_state_after_clicks.json")

    browser.close()
    pw.stop()
    proc.terminate()

if __name__ == "__main__":
    main()
