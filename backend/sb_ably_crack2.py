"""Capture ALL network traffic including XHR, fetch, and check for Ably."""
import json, os, sys, time, subprocess, shutil, urllib.request

SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

PROXY = "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
EVENT_URL = "https://www.sportsbet.com.au/betting/australian-rules/afl/brisbane-lions-v-collingwood/10294344"
CDP_PORT = 9225

def main():
    from sportsbet_cli.client import SportsbetClient
    client = SportsbetClient(proxy=PROXY)
    local_port = client._start_proxy_forwarder(PROXY)

    prof = os.path.join(os.environ.get("TEMP", "/tmp"), "sb_ably2")
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
            break
        except: pass

    from patchright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    page = browser.contexts[0].pages[0]

    all_requests = []

    def on_response(resp):
        url = resp.url
        status = resp.status
        # Capture EVERYTHING that could be Ably or sportsbook data
        if any(k in url.lower() for k in ["ably", "sportsbook", "apigw", "market", "event", "sgm"]):
            try:
                size = len(resp.body())
            except:
                size = 0
            all_requests.append({"url": url[:200], "status": status, "size": size})
            if size > 100:
                print(f"  {status} {size:>8d}b {url[:120]}")

    def on_ws(ws):
        print(f"  WS: {ws.url[:100]}")
        ws.on("framereceived", lambda p: print(f"  WS frame: {str(p)[:100]}"))

    page.on("response", on_response)
    page.on("websocket", on_ws)

    print(f"Loading {EVENT_URL}...")
    page.goto(EVENT_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(20)
    print(f"\nRequests: {len(all_requests)}")

    # Check console for errors
    page.evaluate("console.log('Page loaded, checking for Ably...')")
    ably_check = page.evaluate("""
        () => {
            return {
                hasAbly: typeof window.Ably !== 'undefined',
                hasKPSDK: typeof window.KPSDK !== 'undefined',
                windowKeys: Object.keys(window).filter(k => k.toLowerCase().includes('ably') || k.toLowerCase().includes('store')),
            };
        }
    """)
    print(f"\nWindow state: {json.dumps(ably_check)}")

    # Check if Ably connected via its internal state
    ably_state = page.evaluate("""
        () => {
            // Search for Ably realtime instance
            const keys = Object.keys(window).filter(k => {
                try { return window[k] && window[k].connection; } catch { return false; }
            });
            return keys;
        }
    """)
    print(f"Objects with .connection: {ably_state}")

    # Save requests
    with open("sb_all_requests.json", "w") as f:
        json.dump(all_requests, f, indent=2)
    print(f"\nSaved {len(all_requests)} requests")

    # Show ably-related requests
    ably_reqs = [r for r in all_requests if "ably" in r["url"].lower()]
    print(f"\nAbly requests: {len(ably_reqs)}")
    for r in ably_reqs:
        print(f"  {r['status']} {r['size']:>6d}b {r['url']}")

    browser.close()
    pw.stop()
    proc.terminate()

if __name__ == "__main__":
    main()
