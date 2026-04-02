"""Run LOCALLY — intercept the SGM pricing request from SB's Sport Multi Builder."""
import json, os, sys, time, subprocess, shutil, urllib.request
SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

PROXY = "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
CDP_PORT = 9226

def main():
    from sportsbet_cli.client import SportsbetClient
    client = SportsbetClient(proxy=PROXY)
    local_port = client._start_proxy_forwarder(PROXY)

    prof = os.path.join(os.environ.get("TEMP", "/tmp"), "sb_sgm_intercept")
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
        "https://www.sportsbet.com.au/sport-multi-builder/australian-rules/afl/brisbane-lions-v-collingwood/10294344",
    ], startupinfo=si, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(15):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
            break
        except: pass

    from patchright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    page = browser.contexts[0].pages[0]

    sgm_requests = []

    def on_response(resp):
        url = resp.url
        if "sgm" in url.lower() or "price" in url.lower() or "multi" in url.lower():
            try:
                body = resp.body()
                req = resp.request
                print(f"\n  PRICE: {req.method} {url[:100]}")
                print(f"    Status: {resp.status}, Size: {len(body)}")
                if req.post_data:
                    print(f"    POST body: {req.post_data[:300]}")
                if len(body) > 0 and resp.status == 200:
                    print(f"    Response: {body[:300]}")
                sgm_requests.append({
                    "url": url, "method": req.method, "status": resp.status,
                    "post_data": req.post_data, "response": body.decode('utf-8', errors='replace')[:1000] if body else "",
                })
            except: pass

    page.on("response", on_response)

    print("Waiting 20s for page load...")
    time.sleep(20)

    # Click on a player to add to SGM
    print("Clicking on first disposal market player...")
    page.evaluate("""
        const els = document.querySelectorAll('[data-automation-id]');
        for (const el of els) {
            if (el.textContent.includes('20+ Disposals') || el.getAttribute('data-automation-id')?.includes('disposal')) {
                el.click(); break;
            }
        }
    """)
    time.sleep(3)

    # Click on two specific players' odds
    for player in ["Darcy Wilmot", "Josh Dunkley"]:
        try:
            els = page.locator(f'text="{player}"').all()
            for el in els:
                if el.is_visible():
                    el.click()
                    print(f"  Clicked {player}")
                    time.sleep(3)
                    break
        except: pass

    print(f"\nWaiting 10s for SGM price...")
    time.sleep(10)

    print(f"\n=== Captured {len(sgm_requests)} price requests ===")
    for r in sgm_requests:
        print(json.dumps(r, indent=2)[:500])

    with open("sb_sgm_requests.json", "w") as f:
        json.dump(sgm_requests, f, indent=2)

    browser.close()
    pw.stop()
    proc.terminate()

if __name__ == "__main__":
    main()
