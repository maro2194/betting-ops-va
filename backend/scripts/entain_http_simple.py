"""Simplified Entain HTTP login — no HyperSolutions, no Akamai bypass.

Per memory `project_entain_auth.md`: OAuth2 Hydra login flow works without
anti-bot bypass. Kasada is only on the betting/GraphQL API, not login.

Usage:
  python3 entain_http_simple.py <brand> <username> <password>
Env:
  ENTAIN_PROXY=none|oxylabs|iproyal (default: none — for AU-native hosts)
"""
import asyncio
import json
import logging
import os
import random
import re
import secrets
import sys
import time
from urllib.parse import urlparse, parse_qs

from curl_cffi.requests import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("entain_http_simple")

OAUTH2_CLIENT_IDS = {
    "ladbrokes": "4f075c04-8e6b-4e67-9c00-37fe66415ff1",
    "neds": "89362562-8df2-425c-8bf0-b7b490ba145d",
}

BASES = {
    "ladbrokes": "https://www.ladbrokes.com.au",
    "neds": "https://www.neds.com.au",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)


def build_proxy_url() -> str | None:
    which = os.environ.get("ENTAIN_PROXY", "none").lower()
    if which in ("none", "no", "direct", ""):
        return None
    if which in ("iproyal", "ipr"):
        return "http://FuTUVMvrSTa9cYM8:XZbc7POb6z75bzCb_country-au@geo.iproyal.com:12321"
    sess = random.randint(1000000000, 9999999999)
    return (
        f"http://customer-marolete_86olc-cc-au-sessid-{sess}-sesstime-10:"
        f"K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
    )


def login(brand: str, username: str, password: str) -> dict:
    base = BASES[brand]
    client_id = OAUTH2_CLIENT_IDS[brand]
    proxy_url = build_proxy_url()

    s = Session(impersonate="chrome142")
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}

    state = secrets.token_urlsafe(32)
    auth_url = (
        f"{base}/api/providers/auth/oauth2/auth"
        f"?client_id={client_id}&response_type=code"
        f"&redirect_uri={base}/callback&scope=openid&state={state}"
    )
    headers = {
        "user-agent": USER_AGENT,
        "accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "accept-language": "en-AU,en;q=0.9",
    }

    log.info(f"[1/4] GET {auth_url[:80]}...")
    r1 = s.get(auth_url, headers=headers, timeout=30, allow_redirects=True)
    log.info(f"      status={r1.status_code} final_url={str(r1.url)[:80]}")
    if r1.status_code != 200:
        return {"success": False, "error": f"authorize failed: {r1.status_code}",
                "body": r1.text[:500]}

    html = r1.text
    csrf = re.search(r'name="gorilla\.csrf\.Token"\s+value="([^"]+)"', html)
    challenge = re.search(r'name="challenge"\s+value="([^"]+)"', html)
    if not csrf or not challenge:
        return {"success": False, "error": "csrf/challenge not found in form",
                "body": html[:1000]}
    log.info(f"[2/4] csrf + challenge parsed (challenge={challenge.group(1)[:20]}...)")

    login_post_url = str(r1.url)
    form_data = {
        "gorilla.csrf.Token": csrf.group(1),
        "challenge": challenge.group(1),
        "username": username,
        "password": password,
    }
    headers_post = {
        **headers,
        "content-type": "application/x-www-form-urlencoded",
        "origin": base,
        "referer": login_post_url,
    }

    log.info(f"[3/4] POST login form → {login_post_url[:80]}")
    r2 = s.post(login_post_url, data=form_data, headers=headers_post,
                timeout=30, allow_redirects=True)
    log.info(f"      status={r2.status_code} final_url={str(r2.url)[:80]}")

    # Look for code in final URL or redirect chain
    code = None
    final_url2 = str(r2.url)
    for candidate in [final_url2] + [str(getattr(h, "url", "")) for h in getattr(r2, "history", []) or []]:
        q = parse_qs(urlparse(candidate).query)
        if "code" in q:
            code = q["code"][0]
            break
    # Also check Location header of the final response
    if not code and "code=" in r2.headers.get("location", ""):
        q = parse_qs(urlparse(r2.headers["location"]).query)
        if "code" in q:
            code = q["code"][0]

    if not code:
        return {
            "success": False,
            "error": "no authorization code in redirect chain",
            "post_status": r2.status_code,
            "post_url": final_url2,
            "history_len": len(getattr(r2, "history", []) or []),
            "cookies": {k: (v[:20] + "...") for k, v in dict(s.cookies).items()},
            "body_sample": r2.text[:800],
        }
    log.info(f"[4/4] code received ({code[:20]}...), exchanging for token")

    token_url = f"{base}/api/providers/auth/oauth2/token"
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": f"{base}/callback",
    }
    r3 = s.post(token_url, data=token_data, headers={
        **headers,
        "content-type": "application/x-www-form-urlencoded",
        "origin": base,
        "referer": f"{base}/",
    }, timeout=30)

    if r3.status_code != 200:
        return {"success": False, "error": f"token exchange failed: {r3.status_code}",
                "body": r3.text[:500]}

    tokens = r3.json()
    return {
        "success": True,
        "access_token": tokens.get("access_token", ""),
        "token_type": tokens.get("token_type", ""),
        "expires_in": tokens.get("expires_in", 0),
        "cookies": {k: v for k, v in dict(s.cookies).items()
                     if k in ("hydra_auth", "frontend_session_id")},
    }


async def main():
    if len(sys.argv) != 4:
        print("Usage: entain_http_simple.py <brand> <username> <password>")
        sys.exit(2)
    brand, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"=== ENTAIN HTTP (simple) === brand={brand} user={username}")
    t0 = time.time()
    result = login(brand, username, password)
    print(f"\n--- RESULT ({time.time() - t0:.1f}s) ---")
    print(json.dumps(result, indent=2, default=str)[:2500])
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    asyncio.run(main())
