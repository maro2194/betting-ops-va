"""
Run LOCALLY — uses CDP to capture ALL network traffic including WebSocket.
"""
import json
import os
import sys
import time
import subprocess
import shutil

SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

PROXY = "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
EVENT_URL = "https://www.sportsbet.com.au/betting/australian-rules/afl/brisbane-lions-v-collingwood/10294344"
CDP_PORT = 9223

def main():
    from sportsbet_cli.client import SportsbetClient
    client = SportsbetClient(proxy=PROXY)
    local_port = client._start_proxy_forwarder(PROXY)

    # Fresh profile
    prof = os.path.join(os.environ.get("TEMP", "/tmp"), "sb_cdp_profile")
    if os.path.exists(prof):
        shutil.rmtree(prof, ignore_errors=True)

    chrome = shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 6

    proc = subprocess.Popen([
        chrome,
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run", "--no-default-browser-check",
        f"--user-data-dir={prof}",
        "--window-size=1200,900",
        f"--proxy-server=127.0.0.1:{local_port}",
        "--disable-blink-features=AutomationControlled",
        EVENT_URL,
    ], startupinfo=si, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Wait for page to load and Ably to connect
    print("Chrome launched. Waiting 25s for page + Ably data...")
    time.sleep(25)

    # Connect via CDP and extract the __PRELOADED_STATE__ + any dynamically loaded data
    from patchright.sync_api import sync_playwright
    import urllib.request

    for _ in range(10):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
            break
        except:
            time.sleep(1)

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    page = browser.contexts[0].pages[0]

    # Extract the FULL Redux store state (including dynamically loaded data)
    print("Extracting Redux store...")
    state = page.evaluate("""
        () => {
            // SB stores state in a Redux store accessible via __STORE__ or similar
            if (window.__STORE__) return JSON.parse(JSON.stringify(window.__STORE__.getState()));
            if (window.__store__) return JSON.parse(JSON.stringify(window.__store__.getState()));
            // Try finding the store from React internals
            const root = document.querySelector('#app') || document.querySelector('#root') || document.querySelector('[data-reactroot]');
            if (root && root._reactRootContainer) {
                const fiber = root._reactRootContainer._internalRoot?.current;
                // Walk up to find store
            }
            // Fallback: get what's in the DOM
            return {
                preloadedState: window.__PRELOADED_STATE__ || null,
                apolloState: window.__APOLLO_STATE__ || null,
            };
        }
    """)

    if state:
        raw = json.dumps(state)
        print(f"Store state: {len(raw)} bytes")

        # Check for entities/sportsbook
        sb = None
        if isinstance(state, dict):
            if "entities" in state:
                sb = state["entities"].get("sportsbook", {})
            elif "preloadedState" in state and state["preloadedState"]:
                sb = state["preloadedState"].get("entities", {}).get("sportsbook", {})

        if sb:
            markets = sb.get("markets", {})
            outcomes = sb.get("outcomes", {})
            print(f"Markets: {len(markets)}, Outcomes: {len(outcomes)}")

            # Show ALL market names for our event
            EVENT_ID = "10294344"
            for mid, mkt in markets.items():
                if str(mkt.get("eventId")) == EVENT_ID:
                    name = mkt.get("name", "?")
                    outs = len(mkt.get("outcomeIds", []))
                    sgm = mkt.get("sameGameMultiEnabled", False)
                    print(f"  {'[SGM]' if sgm else '     '} {name} ({outs})")

            # Save full state
            with open("sb_full_state.json", "w") as f:
                json.dump(sb, f)
            print(f"\nSaved full sportsbook state to sb_full_state.json")
        else:
            print("No sportsbook data in store")
            print(f"State keys: {list(state.keys())[:10] if isinstance(state, dict) else 'not dict'}")
    else:
        print("No state extracted")

    browser.close()
    pw.stop()
    proc.terminate()

if __name__ == "__main__":
    main()
