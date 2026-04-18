"""Probe to find a URL that triggers a Kasada 429 challenge with ips.js link in body."""
import random
import re
import sys
from curl_cffi.requests import Session

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36"


def make_session():
    sess = random.randint(1000000000, 9999999999)
    s = Session(impersonate="chrome142")
    s.proxies = {
        "http": f"http://customer-marolete_86olc-cc-au-sessid-{sess}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777",
        "https": f"http://customer-marolete_86olc-cc-au-sessid-{sess}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777",
    }
    return s


def probe(label, method, url, **kwargs):
    s = make_session()
    kwargs.setdefault("headers", {}).setdefault("user-agent", UA)
    kwargs.setdefault("timeout", 20)
    try:
        r = getattr(s, method)(url, **kwargs)
    except Exception as e:
        print(f"[{label}] EXCEPTION: {e}")
        return
    print(f"[{label}] {method.upper()} {url[:80]}")
    print(f"  status={r.status_code}  len={len(r.text)}")
    kpsdk = "yes" if "KPSDK" in r.text else "no"
    ips_hits = re.findall(r'(/[a-f0-9-]+/[a-f0-9-]+/ips\.js[^"\s<]*)', r.text)
    print(f"  KPSDK={kpsdk}  ips.js_links={len(ips_hits)}")
    if ips_hits:
        print(f"  first ips.js: {ips_hits[0][:160]}")
    if not ips_hits and len(r.text) < 500:
        print(f"  body: {r.text[:300]}")


probe("ldb-post-fake-challenge", "post",
      "https://www.ladbrokes.com.au/auth/login?login_challenge=fake",
      data={"a": "b"})

probe("ldb-get-accounts-api", "get",
      "https://www.ladbrokes.com.au/api/v1/accounts/me")

probe("ldb-token-empty", "post",
      "https://www.ladbrokes.com.au/api/providers/auth/oauth2/token",
      data={"grant_type": "authorization_code", "code": "x"})

probe("neds-post-fake-challenge", "post",
      "https://www.neds.com.au/auth/login?login_challenge=fake",
      data={"a": "b"})
