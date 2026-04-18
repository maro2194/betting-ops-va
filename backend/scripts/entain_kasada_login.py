"""Entain login with Kasada bypass via HyperSolutions K4sada product.

Flow:
  1. GET /auth/login?challenge=... (or homepage) → get the 429 Kasada challenge page (or normal)
  2. Parse ips.js link from page
  3. Fetch ips.js script
  4. Call HyperSolutions /payload → returns (base64 payload, headers)
  5. POST to /tl endpoint on target site with payload + headers
  6. Response sets x-kpsdk-ct and x-kpsdk-st
  7. For each protected request (including login POST):
     - Compute x-kpsdk-cd via HyperSolutions /cd (PoW)
     - Send headers x-kpsdk-ct + x-kpsdk-cd on the request

Usage:   python3 entain_kasada_login.py <brand> <username> <password>
Env:     HYPERSOLUTIONS_API_KEY (required)
         ENTAIN_PROXY=none|oxylabs|iproyal (default: none)
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
from hyper_sdk import SessionAsync as HyperSession
from hyper_sdk.kasada import parse_kasada_script_path
from hyper_sdk.kasada_input import KasadaPayloadInput, KasadaPowInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("entain_kasada")

HYPER_API_KEY = os.environ.get(
    "HYPERSOLUTIONS_API_KEY", "2f71b97d-0289-47e5-ba8e-d60c321f959a"
)

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
ACCEPT_LANG = "en-AU,en;q=0.9"


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


def _get_ip(session: Session) -> str:
    try:
        r = session.get("https://api.ipify.org", timeout=10)
        return r.text.strip()
    except Exception:
        return ""


async def kasada_bootstrap(session: Session, base_url: str, my_ip: str,
                            probe_url: str | None = None) -> dict:
    """Do the Kasada bypass handshake. Returns {ct, st, domain}.

    Kasada script path is a stable Entain tenant — same UUIDs for Ladbrokes
    and Neds. We visit the homepage (establishes __cf_bm), then fetch ips.js
    directly without triggering a 429 challenge. This avoids session taint.
    """
    parsed = urlparse(base_url)
    domain = parsed.netloc

    browser_headers = {
        "user-agent": USER_AGENT,
        "accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "accept-language": ACCEPT_LANG,
        "sec-ch-ua": '"Google Chrome";v="142", "Chromium";v="142", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "upgrade-insecure-requests": "1",
        "sec-fetch-site": "none",
        "sec-fetch-mode": "navigate",
        "sec-fetch-user": "?1",
        "sec-fetch-dest": "document",
    }

    log.info("[Kasada 1/4] GET homepage (establish __cf_bm, no challenge)")
    r_home = session.get(base_url, headers=browser_headers, timeout=30)
    log.info(f"  homepage status={r_home.status_code}")

    # Stable Kasada path for Entain (Ladbrokes + Neds both use this tenant)
    ips_path = (
        "/149e9513-01fa-4fb0-aad4-566afd725d1b/"
        "2d206a39-8ed7-437e-a3be-862e0f06eea3/ips.js"
    )
    ips_link = f"{parsed.scheme}://{parsed.netloc}{ips_path}"
    log.info(f"[Kasada 2/4] ips.js: {ips_link[:120]}")

    log.info("[Kasada 3/4] Fetching ips.js script")
    script_headers = {
        **browser_headers,
        "accept": "*/*",
        "referer": base_url,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-dest": "script",
    }
    r_script = session.get(ips_link, headers=script_headers, timeout=30)
    script_content = r_script.text
    log.info(f"  script size={len(script_content)}")

    log.info("[Kasada 4/4] HyperSolutions /payload → /tl POST")
    async with HyperSession(HYPER_API_KEY) as hs:
        payload, hs_headers = await hs.generate_kasada_payload(KasadaPayloadInput(
            user_agent=USER_AGENT,
            ips_link=ips_link,
            script=script_content,
            accept_language=ACCEPT_LANG,
            ip=my_ip,
        ))
    log.info(f"  got payload ({len(payload)} chars) + {len(hs_headers)} hs_headers")

    # /tl is same directory as /ips.js
    tl_url = ips_link.rsplit("/", 1)[0] + "/tl"
    tl_headers = {
        **browser_headers,
        "accept": "*/*",
        "content-type": "application/octet-stream",
        "origin": base_url.rstrip("/"),
        "referer": base_url,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        **hs_headers,  # HyperSolutions provides additional x-kpsdk-* headers
    }
    # Payload is base64 encoded — curl_cffi accepts str or bytes
    import base64
    r_tl = session.post(tl_url, headers=tl_headers, data=base64.b64decode(payload), timeout=30)
    log.info(f"  /tl POST status={r_tl.status_code}")
    log.info(f"  /tl ALL response headers:")
    for k, v in r_tl.headers.items():
        vshort = v[:80] + "..." if len(v) > 80 else v
        log.info(f"    {k}: {vshort}")

    ct = r_tl.headers.get("x-kpsdk-ct")
    st = r_tl.headers.get("x-kpsdk-st")
    fc = r_tl.headers.get("x-kpsdk-fc")
    log.info(f"  parsed: ct={'yes' if ct else 'NO'} ({len(ct) if ct else 0})  st={st!r}  fc={fc!r}")

    if not ct or not st:
        body = r_tl.text[:500] if hasattr(r_tl, "text") else repr(r_tl.content)[:500]
        raise RuntimeError(f"Kasada /tl did not return ct+st headers. "
                           f"status={r_tl.status_code} body={body}")

    result = {"ct": ct, "st": int(st), "domain": domain}
    if fc:
        result["fc"] = fc
    return result


async def compute_pow(kasada_state: dict) -> str:
    """Compute x-kpsdk-cd PoW token for the next request."""
    async with HyperSession(HYPER_API_KEY) as hs:
        cd = await hs.generate_kasada_pow(KasadaPowInput(
            st=kasada_state["st"],
            ct=kasada_state["ct"],
            domain=kasada_state["domain"],
            fc=kasada_state.get("fc", ""),
        ))
    return cd


async def login(brand: str, username: str, password: str) -> dict:
    base = BASES[brand]
    client_id = OAUTH2_CLIENT_IDS[brand]
    proxy_url = build_proxy_url()

    s = Session(impersonate="chrome142")
    if proxy_url:
        s.proxies = {"http": proxy_url, "https": proxy_url}
    log.info(f"Proxy: {proxy_url or 'DIRECT'}")

    my_ip = _get_ip(s)
    log.info(f"Our IP: {my_ip}")

    # === Kasada bootstrap (no probe — homepage 200 + fetch ips.js direct) ===
    kasada = await kasada_bootstrap(s, base + "/", my_ip)
    log.info(f"Kasada handshake OK — ct.len={len(kasada['ct'])} st={kasada['st']}")

    # === OAuth2 authorize ===
    state = secrets.token_urlsafe(32)
    auth_url = (
        f"{base}/api/providers/auth/oauth2/auth"
        f"?client_id={client_id}&response_type=code"
        f"&redirect_uri={base}/callback&scope=openid&state={state}"
    )
    base_headers = {
        "user-agent": USER_AGENT,
        "accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "accept-language": ACCEPT_LANG,
        "sec-ch-ua": '"Google Chrome";v="142", "Chromium";v="142", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

    async def with_kasada(extra_headers: dict) -> dict:
        """Add fresh x-kpsdk-ct + x-kpsdk-cd to a headers dict."""
        cd = await compute_pow(kasada)
        return {
            **extra_headers,
            "x-kpsdk-ct": kasada["ct"],
            "x-kpsdk-cd": cd,
            "x-kpsdk-v": "j-1.2.308",  # Kasada SDK version — seen in challenge URLs
        }

    log.info("[Login 1/4] GET /oauth2/auth")
    h = await with_kasada(base_headers)
    r1 = s.get(auth_url, headers=h, timeout=30, allow_redirects=True)
    log.info(f"  status={r1.status_code} url={str(r1.url)[:120]}")
    # Kasada may rotate ct on each response
    new_ct = r1.headers.get("x-kpsdk-ct")
    new_st = r1.headers.get("x-kpsdk-st")
    new_fc = r1.headers.get("x-kpsdk-fc")
    log.info(f"  response kpsdk headers: ct={'rotated' if new_ct and new_ct != kasada['ct'] else 'same'}  st={new_st}  fc={new_fc}")
    if new_ct:
        kasada["ct"] = new_ct
    if new_st:
        kasada["st"] = int(new_st)
    if new_fc:
        kasada["fc"] = new_fc
    if r1.status_code != 200:
        return {"success": False, "step": "authorize", "status": r1.status_code,
                "body": r1.text[:800]}

    csrf = re.search(r'name="gorilla\.csrf\.Token"\s+value="([^"]+)"', r1.text)
    challenge = re.search(r'name="challenge"\s+value="([^"]+)"', r1.text)
    if not csrf or not challenge:
        return {"success": False, "step": "parse_form",
                "body": r1.text[:1200]}
    log.info(f"[Login 2/4] Got CSRF + challenge")

    login_post_url = str(r1.url)
    form_data = {
        "gorilla.csrf.Token": csrf.group(1),
        "challenge": challenge.group(1),
        "username": username,
        "password": password,
    }
    h = await with_kasada({
        **base_headers,
        "content-type": "application/x-www-form-urlencoded",
        "origin": base,
        "referer": login_post_url,
    })
    log.info("[Login 3/4] POST login form (with Kasada headers)")
    r2 = s.post(login_post_url, data=form_data, headers=h,
                timeout=30, allow_redirects=True)
    log.info(f"  status={r2.status_code} url={str(r2.url)[:120]}")
    log.info("  POST response all headers:")
    for k, v in r2.headers.items():
        if "kpsdk" in k.lower() or "kasada" in k.lower() or "cf-" in k.lower() or "set-cookie" in k.lower():
            vshort = v[:120] + "..." if len(v) > 120 else v
            log.info(f"    {k}: {vshort}")
    post_ct = r2.headers.get("x-kpsdk-ct")
    post_r = r2.headers.get("x-kpsdk-r")
    log.info(f"  x-kpsdk-r (reason): {post_r}")

    code = None
    final_url = str(r2.url)
    for candidate in [final_url] + [str(getattr(h2, "url", "")) for h2 in getattr(r2, "history", []) or []]:
        q = parse_qs(urlparse(candidate).query)
        if "code" in q:
            code = q["code"][0]
            break
    if not code and "code=" in r2.headers.get("location", ""):
        code = parse_qs(urlparse(r2.headers["location"]).query)["code"][0]

    if not code:
        return {
            "success": False, "step": "login_post",
            "post_status": r2.status_code,
            "final_url": final_url,
            "cookies": {k: (v[:20] + "...") for k, v in dict(s.cookies).items()},
            "body": r2.text[:1200],
        }
    log.info(f"  got code ({code[:16]}...)")

    log.info("[Login 4/4] Exchange code for access_token")
    token_url = f"{base}/api/providers/auth/oauth2/token"
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": f"{base}/callback",
    }
    h = await with_kasada({
        **base_headers,
        "content-type": "application/x-www-form-urlencoded",
        "accept": "application/json",
        "origin": base,
        "referer": f"{base}/",
    })
    r3 = s.post(token_url, data=token_data, headers=h, timeout=30)
    log.info(f"  status={r3.status_code}")
    if r3.status_code != 200:
        return {"success": False, "step": "token_exchange",
                "status": r3.status_code, "body": r3.text[:800]}

    tokens = r3.json()
    at = tokens.get("access_token", "")
    return {
        "success": True,
        "access_token": at,
        "token_len": len(at),
        "token_type": tokens.get("token_type", ""),
        "expires_in": tokens.get("expires_in", 0),
        "cookies": {k: v for k, v in dict(s.cookies).items()
                     if k in ("hydra_auth", "frontend_session_id")},
    }


async def main():
    if len(sys.argv) != 4:
        print("Usage: entain_kasada_login.py <brand> <username> <password>")
        sys.exit(2)
    brand, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    print(f"=== ENTAIN (Kasada-aware) === brand={brand} user={username}")
    t0 = time.time()
    try:
        result = await login(brand, username, password)
    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    print(f"\n--- RESULT ({time.time() - t0:.1f}s) ---")
    safe = dict(result)
    if "access_token" in safe:
        safe["access_token_prefix"] = safe["access_token"][:40] + "..."
        del safe["access_token"]
    print(json.dumps(safe, indent=2, default=str)[:2500])
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    asyncio.run(main())
