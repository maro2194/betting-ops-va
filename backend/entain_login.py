"""
Entain (Ladbrokes/Neds) HTTP-Only Login with Akamai Bypass.
Uses curl_cffi for TLS fingerprinting + Hyper Solutions SDK for Akamai + OAuth2 Hydra flow.

Flow:
1. Fetch homepage → parse Akamai sensor script path
2. Generate + POST 3 sensors via HyperSolutions (same as TAB)
3. Start OAuth2 authorize → get login_challenge
4. Parse login form (CSRF token + challenge)
5. POST login with username/password
6. Follow redirect chain → get authorization code
7. Exchange code for access_token
"""
import json
import logging
import re
import time
from typing import Optional
from urllib.parse import urlparse, parse_qs

from curl_cffi.requests import Session
from hyper_sdk import SessionAsync as HyperSession, SensorInput
from hyper_sdk.akamai import parse_akamai_script_path, is_cookie_valid

logger = logging.getLogger(__name__)

# Browser Fingerprint
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
SEC_CH_UA = '"Google Chrome";v="142", "Chromium";v="142", "Not A(Brand";v="24"'
SEC_CH_UA_PLATFORM = '"Windows"'
ACCEPT_LANGUAGE = "en-AU,en;q=0.9"

# OAuth2 Configuration — each brand has its own client ID
OAUTH2_CLIENT_IDS = {
    "ladbrokes": "4f075c04-8e6b-4e67-9c00-37fe66415ff1",
    "neds": "89362562-8df2-425c-8bf0-b7b490ba145d",
}

# HyperSolutions API key (same as TAB)
import os
HYPER_API_KEY = os.getenv("HYPERSOLUTIONS_API_KEY", "2f71b97d-0289-47e5-ba8e-d60c321f959a")

ENTAIN_BRANDS = {
    "ladbrokes": {
        "base_url": "https://www.ladbrokes.com.au/",
        "api_base": "https://api.ladbrokes.com.au",
    },
    "neds": {
        "base_url": "https://www.neds.com.au/",
        "api_base": "https://api.neds.com.au",
    },
}


class EntainLogin:
    """HTTP-only Entain login with Akamai bypass + OAuth2 Hydra authentication."""

    def __init__(self, api_key: str, username: str, password: str,
                 brand: str = "ladbrokes", proxy: Optional[str] = None):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.brand = brand.lower()
        self.brand_config = ENTAIN_BRANDS.get(self.brand, ENTAIN_BRANDS["ladbrokes"])
        self.base_url = self.brand_config["base_url"]
        self.proxy = proxy
        self.session = Session(impersonate="chrome142")
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.ip = ""
        self.sensor_endpoint = ""
        self.sensor_script = ""
        self.sensor_context = ""
        self.access_token = ""

    async def login(self) -> str:
        logger.info(f"Starting {self.brand} login for {self.username}...")
        await self._oauth2_login()
        if not self.access_token:
            raise Exception("Failed to obtain access token")
        logger.info(f"{self.brand} login successful!")
        return self.access_token

    def _get_public_ip(self) -> str:
        try:
            resp = self.session.get("https://api.ipify.org", timeout=10)
            return resp.text.strip()
        except Exception as e:
            logger.warning(f"Failed to get IP: {e}")
            return ""

    # ─── Akamai Bypass (same pattern as TAB) ─────────────────────────

    async def _bypass_akamai(self) -> None:
        logger.info(f"Fetching {self.brand} homepage...")
        html = self._fetch_page(self.base_url)
        logger.info("Parsing Akamai sensor endpoint...")
        if not self._parse_sensor_endpoint(html):
            raise Exception("Failed to find Akamai sensor endpoint")
        logger.info(f"Endpoint: {self.sensor_endpoint}")
        logger.info("Fetching sensor script...")
        self._fetch_sensor_script()
        logger.info("Generating and posting sensors...")
        await self._post_sensors()
        abck = self._get_cookie("_abck")
        if "~0~" in abck:
            logger.info("Akamai bypass successful (cookie contains ~0~)")
        else:
            logger.warning(f"Cookie doesn't contain ~0~ signal, continuing... (_abck={abck[:50]}...)")

    def _fetch_page(self, url: str) -> str:
        headers = {
            "sec-ch-ua": SEC_CH_UA, "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
            "upgrade-insecure-requests": "1", "user-agent": USER_AGENT,
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "sec-fetch-site": "none", "sec-fetch-mode": "navigate",
            "sec-fetch-user": "?1", "sec-fetch-dest": "document",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": ACCEPT_LANGUAGE,
        }
        resp = self.session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text

    def _parse_sensor_endpoint(self, html: str) -> bool:
        try:
            script_path = parse_akamai_script_path(html)
            self.sensor_endpoint = f"{self.base_url.rstrip('/')}/{script_path.lstrip('/')}"
            return True
        except Exception as e:
            logger.error(f"Parse error: {e}")
            return False

    def _fetch_sensor_script(self) -> None:
        headers = {
            "sec-ch-ua": SEC_CH_UA, "sec-ch-ua-mobile": "?0",
            "user-agent": USER_AGENT, "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
            "accept": "*/*", "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "no-cors", "sec-fetch-dest": "script",
            "referer": self.base_url,
            "accept-encoding": "gzip, deflate, br",
            "accept-language": ACCEPT_LANGUAGE,
        }
        resp = self.session.get(self.sensor_endpoint, headers=headers, timeout=30)
        self.sensor_script = resp.text
        logger.info(f"Script size: {len(self.sensor_script)} bytes")

    async def _post_sensors(self) -> None:
        hyper_session = HyperSession(self.api_key)
        try:
            for i in range(3):
                abck = self._get_cookie("_abck")
                bmsz = self._get_cookie("bm_sz")
                sensor_data, sensor_context = await hyper_session.generate_sensor_data(
                    SensorInput(
                        abck=abck, bmsz=bmsz, version="3",
                        page_url=self.base_url, user_agent=USER_AGENT,
                        script_url=self.sensor_endpoint,
                        accept_language=ACCEPT_LANGUAGE,
                        ip=self.ip, context=self.sensor_context,
                        script=self.sensor_script if i == 0 else "",
                    )
                )
                self.sensor_context = sensor_context
                self._post_sensor_data(sensor_data)
                logger.info(f"Sensor {i+1}/3 posted")
                abck = self._get_cookie("_abck")
                if is_cookie_valid(abck, i):
                    logger.info(f"Cookie valid after {i+1} sensor(s)")
                    break
                time.sleep(0.3)
        finally:
            await hyper_session.close()

    def _post_sensor_data(self, sensor_data: str) -> None:
        headers = {
            "sec-ch-ua": SEC_CH_UA, "sec-ch-ua-platform": SEC_CH_UA_PLATFORM,
            "sec-ch-ua-mobile": "?0", "user-agent": USER_AGENT,
            "content-type": "text/plain;charset=UTF-8", "accept": "*/*",
            "origin": self.base_url.rstrip("/"), "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
            "referer": self.base_url,
            "accept-encoding": "gzip, deflate, br",
            "accept-language": ACCEPT_LANGUAGE,
        }
        payload = json.dumps({"sensor_data": sensor_data})
        self.session.post(self.sensor_endpoint, headers=headers, data=payload, timeout=30)

    # ─── OAuth2 Hydra Login ──────────────────────────────────────────

    async def _oauth2_login(self) -> None:
        """OAuth2 Authorization Code flow via Hydra."""
        base = self.base_url.rstrip("/")
        client_id = OAUTH2_CLIENT_IDS.get(self.brand, OAUTH2_CLIENT_IDS["ladbrokes"])

        # Step 1: Start OAuth2 authorize flow
        import secrets
        state = secrets.token_urlsafe(32)
        auth_url = (
            f"{base}/api/providers/auth/oauth2/auth"
            f"?client_id={client_id}"
            f"&response_type=code"
            f"&redirect_uri={base}/callback"
            f"&scope=openid"
            f"&state={state}"
        )
        logger.info("Starting OAuth2 authorize...")
        headers = {
            "user-agent": USER_AGENT,
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": self.base_url,
            "accept-language": ACCEPT_LANGUAGE,
        }

        # Follow redirects to get to login form
        resp = self.session.get(auth_url, headers=headers, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            raise Exception(f"OAuth2 authorize failed: HTTP {resp.status_code}")

        login_html = resp.text
        final_url = str(resp.url)
        logger.info(f"Login page URL: {final_url[:100]}")

        # Step 2: Parse login form for CSRF token and challenge
        csrf_match = re.search(r'name="gorilla\.csrf\.Token"\s+value="([^"]+)"', login_html)
        challenge_match = re.search(r'name="challenge"\s+value="([^"]+)"', login_html)

        if not csrf_match:
            # Try alternative patterns
            csrf_match = re.search(r'gorilla\.csrf\.Token["\s]+value="([^"]+)"', login_html)
        if not challenge_match:
            challenge_match = re.search(r'challenge["\s]+value="([^"]+)"', login_html)

        if not csrf_match or not challenge_match:
            logger.error(f"Login form parse failed. CSRF: {bool(csrf_match)}, Challenge: {bool(challenge_match)}")
            logger.debug(f"Login HTML snippet: {login_html[:1000]}")
            raise Exception("Failed to parse login form (CSRF token or challenge missing)")

        csrf_token = csrf_match.group(1)
        challenge = challenge_match.group(1)
        logger.info(f"Got CSRF token and challenge ({challenge[:20]}...)")

        # Step 3: POST login form
        login_post_url = final_url  # POST back to the same URL
        form_data = {
            "gorilla.csrf.Token": csrf_token,
            "challenge": challenge,
            "username": self.username,
            "password": self.password,
        }
        headers_post = {
            "user-agent": USER_AGENT,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "origin": base,
            "referer": final_url,
            "accept-language": ACCEPT_LANGUAGE,
        }

        logger.info("Posting login credentials...")
        resp2 = self.session.post(login_post_url, data=form_data, headers=headers_post,
                                   timeout=30, allow_redirects=True)

        # Step 4: Follow redirect chain to get authorization code
        # The final redirect should be to redirect_uri with ?code=...
        final_url2 = str(resp2.url)
        logger.info(f"Post-login URL: {final_url2[:100]}")

        # Extract code from URL
        code = None
        parsed = urlparse(final_url2)
        params = parse_qs(parsed.query)
        if "code" in params:
            code = params["code"][0]
            logger.info(f"Got authorization code: {code[:20]}...")

        # Also check redirect history
        if not code and hasattr(resp2, 'history'):
            for r in resp2.history:
                r_url = str(r.url) if hasattr(r, 'url') else str(r.headers.get('location', ''))
                if 'code=' in r_url:
                    parsed_r = urlparse(r_url)
                    params_r = parse_qs(parsed_r.query)
                    if "code" in params_r:
                        code = params_r["code"][0]
                        logger.info(f"Got code from redirect history: {code[:20]}...")
                        break

        # Check response headers for redirect with code
        if not code:
            location = resp2.headers.get("location", "")
            if "code=" in location:
                parsed_loc = urlparse(location)
                params_loc = parse_qs(parsed_loc.query)
                if "code" in params_loc:
                    code = params_loc["code"][0]

        if not code:
            # Note: we intentionally do NOT retry GET /auth here — Hydra rate-limits
            # successive /auth calls (observed: second call returns 429). The right
            # answer is to fix whichever step produced the current "no code" state.
            if self.session.cookies.get("hydra_auth") or self.session.cookies.get("frontend_session_id"):
                logger.warning("hydra_auth cookie present but no code in redirect chain — response inspection needed (see dump below)")

        if not code:
            logger.error(f"No authorization code found. Final URL: {final_url2}")
            logger.error(f"Response status: {resp2.status_code}")
            logger.error(f"Response headers: {dict(resp2.headers)}")
            logger.error(f"Cookies: {dict(self.session.cookies)}")
            # Dump full body + redirect chain to help debug
            try:
                import tempfile, os
                dump_path = os.path.join(tempfile.gettempdir(), f"entain_dump_{self.brand}_{int(time.time())}.html")
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(f"<!-- URL: {final_url2} -->\n")
                    f.write(f"<!-- Status: {resp2.status_code} -->\n")
                    f.write(f"<!-- Headers: {dict(resp2.headers)} -->\n")
                    hist = []
                    for h in getattr(resp2, "history", []) or []:
                        try:
                            hist.append(f"  {h.status_code} {h.url} -> Location: {h.headers.get('location', '')}")
                        except Exception:
                            pass
                    f.write(f"<!-- History ({len(hist)}): -->\n")
                    for line in hist:
                        f.write(f"<!-- {line} -->\n")
                    f.write("<!-- BODY -->\n")
                    f.write(resp2.text[:20000])
                logger.error(f"Full dump written to: {dump_path}")
            except Exception as dump_err:
                logger.error(f"Failed to dump: {dump_err}")
            raise Exception("Failed to obtain authorization code")

        # Step 5: Exchange code for access token
        token_url = f"{base}/api/providers/auth/oauth2/token"
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": f"{base}/callback",
        }
        headers_token = {
            "user-agent": USER_AGENT,
            "content-type": "application/x-www-form-urlencoded",
            "accept": "application/json",
            "origin": base,
            "referer": f"{base}/",
            "accept-language": ACCEPT_LANGUAGE,
        }

        logger.info("Exchanging code for access token...")
        resp_token = self.session.post(token_url, data=token_data, headers=headers_token, timeout=30)

        if resp_token.status_code != 200:
            logger.error(f"Token exchange failed: {resp_token.status_code} {resp_token.text[:500]}")
            raise Exception(f"Token exchange failed: {resp_token.status_code}")

        tokens = resp_token.json()
        self.access_token = tokens.get("access_token", "")
        if self.access_token:
            logger.info(f"Got access token ({len(self.access_token)} chars)")
        else:
            logger.error(f"No access_token in response: {tokens}")
            raise Exception("Token response missing access_token")

    def _get_cookie(self, name: str) -> str:
        return self.session.cookies.get(name, "")

    def close(self):
        self.session.close()


async def entain_browser_login(username: str, password: str, brand: str = "ladbrokes",
                                proxy_url: Optional[str] = None) -> dict:
    """
    HTTP-only Entain login. Returns dict with access_token and session data.
    """
    login = EntainLogin(
        api_key=HYPER_API_KEY,
        username=username,
        password=password,
        brand=brand,
        proxy=proxy_url if proxy_url else None,
    )
    try:
        token = await login.login()
        return {
            "success": True,
            "access_token": token,
            "brand": brand,
            "username": username,
            "proxy_url": proxy_url or "",
            "cookies": {
                "hydra_auth": login._get_cookie("hydra_auth"),
                "frontend_session_id": login._get_cookie("frontend_session_id"),
            },
        }
    except Exception as e:
        logger.error(f"Entain login failed for {username}@{brand}: {e}")
        return {
            "success": False,
            "error": str(e),
            "brand": brand,
            "username": username,
        }
    finally:
        login.close()
