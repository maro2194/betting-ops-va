"""Check if Ladbrokes Kasada uses /mfc endpoint (for fc token)."""
import random
from curl_cffi.requests import Session

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36"

sess = random.randint(1000000000, 9999999999)
s = Session(impersonate="chrome142")
s.proxies = {
    "https": f"http://customer-marolete_86olc-cc-au-sessid-{sess}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777",
}

# Known UUID path for Ladbrokes/Neds Kasada
mfc_url = "https://www.ladbrokes.com.au/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/mfc"
r = s.get(mfc_url, headers={"user-agent": UA}, timeout=20)
print(f"mfc GET status={r.status_code} len={len(r.text)}")
print(f"  response headers (kpsdk): {[(k, v[:60]+'...' if len(v)>60 else v) for k, v in r.headers.items() if 'kpsdk' in k.lower()]}")
print(f"  body[:400]: {r.text[:400]}")

# Also check if the site has native-kasada feature flag + needs the mfc
print()
for path in ["/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/mfp", "/kpsdk/mfc"]:
    url = f"https://www.ladbrokes.com.au{path}"
    r = s.get(url, headers={"user-agent": UA}, timeout=20)
    print(f"{path}: status={r.status_code} kpsdk_hdrs={[k for k in r.headers if 'kpsdk' in k.lower()]}")
