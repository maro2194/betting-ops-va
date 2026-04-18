"""Check what Kasada cookies homepage sets — try to avoid triggering a challenge."""
import random
from curl_cffi.requests import Session

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36"
KASADA_PATH = "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3"

for site in ["ladbrokes", "neds"]:
    print(f"\n=== {site} ===")
    sess = random.randint(1000000000, 9999999999)
    s = Session(impersonate="chrome142")
    s.proxies = {
        "https": f"http://customer-marolete_86olc-cc-au-sessid-{sess}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777",
    }
    r = s.get(f"https://www.{site}.com.au/", headers={"user-agent": UA}, timeout=20)
    print(f"homepage status={r.status_code}")
    print(f"  Kasada cookies: {[k for k in dict(s.cookies) if 'KP' in k or 'kasada' in k.lower()]}")
    print(f"  All cookies: {list(dict(s.cookies).keys())}")

    # Try fetching ips.js directly (no KP_UIDz param) — maybe works?
    ips_url = f"https://www.{site}.com.au{KASADA_PATH}/ips.js"
    r2 = s.get(ips_url, headers={"user-agent": UA, "referer": f"https://www.{site}.com.au/"}, timeout=20)
    print(f"ips.js (no params) status={r2.status_code} len={len(r2.text)}")

    # Try fetching /mfc
    mfc_url = f"https://www.{site}.com.au{KASADA_PATH}/mfc"
    r3 = s.get(mfc_url, headers={"user-agent": UA, "referer": f"https://www.{site}.com.au/"}, timeout=20)
    print(f"mfc status={r3.status_code} len={len(r3.text)} kpsdk_hdrs={[k for k in r3.headers if 'kpsdk' in k.lower()]}")
    for k, v in r3.headers.items():
        if "kpsdk" in k.lower():
            print(f"    {k}: {v[:80]}")
