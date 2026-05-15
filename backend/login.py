#!/usr/bin/env python3
"""
TAB.com.au HTTP-Only Login with Akamai Bypass.
Uses curl_cffi for TLS fingerprinting + Hyper Solutions SDK for Akamai + Auth0 ROPC.
"""
import json
import time
import logging
from typing import Optional
import asyncio

from curl_cffi.requests import Session
from hyper_sdk import SessionAsync as HyperSession, SensorInput
from hyper_sdk.akamai import parse_akamai_script_path, is_cookie_valid

logger = logging.getLogger(__name__)


class MFARequiredException(Exception):
    """Raised when Auth0 requires MFA. Carries state needed to complete the flow."""
    def __init__(self, mfa_token: str, oob_code: str = ""):
        self.mfa_token = mfa_token
        self.oob_code = oob_code
        super().__init__("MFA required")

# Auth0 Configuration
AUTH0_DOMAIN = "https://login.tab.com.au"
AUTH0_CLIENT_ID = "npgc7BsgmFe2VN3hOPPgalyxPfh0crzB"
AUTH0_AUDIENCE = "https://api.beta.tab.com.au"
AUTH0_CONNECTION = "Username-Password-Authentication"

# Browser Fingerprint
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
SEC_CH_UA = '"Google Chrome";v="142", "Chromium";v="142", "Not A(Brand";v="24"'
SEC_CH_UA_PLATFORM = '"Windows"'
ACCEPT_LANGUAGE = "en-AU,en;q=0.9"
TAB_BASE_URL = "https://www.tab.com.au/"
HYPER_API_KEY = "2f71b97d-0289-47e5-ba8e-d60c321f959a"


class TABLogin:
    """HTTP-only TAB login with Akamai bypass + Auth0 authentication."""

    def __init__(self, api_key: str, email: str, password: str, proxy: Optional[str] = None):
        self.api_key = api_key
        self.email = email
        self.password = password
        self.proxy = proxy
        self.session = Session(impersonate="chrome142")
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.ip = ""
        self.sensor_endpoint = ""
        self.sensor_script = ""
        self.sensor_context = ""
        self.access_token = ""
        self.id_token = ""

    async def login(self) -> str:
        logger.info("Starting HTTP-only TAB login...")
        self.ip = self._get_public_ip()
        logger.info(f"Public IP: {self.ip}")
        await self._bypass_akamai()
        await self._auth0_login()
        if not self.access_token:
            raise Exception("Failed to obtain access token")
        logger.info("Login successful!")
        return self.access_token

    def _get_public_ip(self) -> str:
        try:
            resp = self.session.get("https://api.ipify.org", timeout=10)
            return resp.text.strip()
        except Exception as e:
            logger.warning(f"Failed to get IP: {e}")
            return ""

    async def _bypass_akamai(self) -> None:
        logger.info("Fetching TAB homepage...")
        html = self._fetch_page(TAB_BASE_URL)
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
            logger.warning("Cookie doesn't contain ~0~ signal, continuing...")

    async def _auth0_login(self) -> None:
        logger.info("Auth0 Resource Owner Password Grant...")
        token_url = f"{AUTH0_DOMAIN}/oauth/token"
        payload = {
            "grant_type": "password",
            "username": self.email,
            "password": self.password,
            "client_id": AUTH0_CLIENT_ID,
            "audience": AUTH0_AUDIENCE,
            "scope": "openid profile email",
            "connection": AUTH0_CONNECTION
        }
        headers = {
            "content-type": "application/json",
            "user-agent": USER_AGENT,
            "accept": "application/json",
            "origin": TAB_BASE_URL,
            "referer": TAB_BASE_URL,
        }
        resp = self.session.post(token_url, json=payload, headers=headers, timeout=30)
        if resp.status_code == 200:
            tokens = resp.json()
            self.access_token = tokens.get("access_token", "")
            self.id_token = tokens.get("id_token", "")
            if self.access_token:
                logger.info("Got access token from Auth0")
                return
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 403 and body.get("error") == "mfa_required":
            mfa_token = body["mfa_token"]
            logger.info("MFA required — triggering SMS challenge...")
            oob_code = self._trigger_mfa_challenge(mfa_token, headers)
            raise MFARequiredException(mfa_token, oob_code)
        logger.error(f"Auth0 login failed: {resp.status_code} {resp.text[:500]}")
        raise Exception(f"Auth0 login failed: {resp.status_code}")

    def _trigger_mfa_challenge(self, mfa_token: str, headers: dict) -> str:
        resp = self.session.post(
            f"{AUTH0_DOMAIN}/mfa/challenge",
            json={"mfa_token": mfa_token, "client_id": AUTH0_CLIENT_ID, "challenge_type": "oob"},
            headers=headers, timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"MFA SMS challenge sent (type={data.get('challenge_type')})")
            return data.get("oob_code", "")
        logger.error(f"MFA challenge request failed: {resp.status_code} {resp.text[:300]}")
        raise Exception(f"MFA challenge failed: {resp.status_code}")

    def complete_mfa(self, mfa_token: str, oob_code: str, otp_code: str) -> None:
        headers = {
            "content-type": "application/json",
            "user-agent": USER_AGENT,
            "accept": "application/json",
            "origin": TAB_BASE_URL,
            "referer": TAB_BASE_URL,
        }
        resp = self.session.post(
            f"{AUTH0_DOMAIN}/oauth/token",
            json={
                "grant_type": "http://auth0.com/oauth/grant-type/mfa-oob",
                "client_id": AUTH0_CLIENT_ID,
                "mfa_token": mfa_token,
                "oob_code": oob_code,
                "binding_code": otp_code,
            },
            headers=headers, timeout=30,
        )
        if resp.status_code == 200:
            tokens = resp.json()
            self.access_token = tokens.get("access_token", "")
            self.id_token = tokens.get("id_token", "")
            if self.access_token:
                logger.info("MFA verification successful — got access token")
                return
        logger.error(f"MFA verify failed: {resp.status_code} {resp.text[:500]}")
        raise Exception(f"MFA verification failed: {resp.status_code}")

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
            self.sensor_endpoint = f"{TAB_BASE_URL}{script_path}"
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
            "referer": TAB_BASE_URL,
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
                        page_url=TAB_BASE_URL, user_agent=USER_AGENT,
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
            "origin": TAB_BASE_URL, "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
            "referer": TAB_BASE_URL,
            "accept-encoding": "gzip, deflate, br",
            "accept-language": ACCEPT_LANGUAGE,
        }
        payload = json.dumps({"sensor_data": sensor_data})
        self.session.post(self.sensor_endpoint, headers=headers, data=payload, timeout=30)

    def _get_cookie(self, name: str) -> str:
        return self.session.cookies.get(name, "")

    def close(self):
        self.session.close()


def decode_token_claims(token: str) -> dict:
    """Decode JWT payload without verification."""
    import base64
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        return {}


async def browser_login(email: str, password: str, proxy_url: str) -> dict:
    """
    HTTP-only TAB login. Drop-in replacement for the old browser-based login.
    Returns dict with: token, account_number, customer_id, email, proxy_url
    If MFA is required, returns dict with mfa_required=True and state to complete later.
    """
    login = TABLogin(
        api_key=HYPER_API_KEY,
        email=email,
        password=password,
        proxy=proxy_url if proxy_url else None
    )
    try:
        token = await login.login()
        claims = decode_token_claims(token)
        customer_id = str(claims.get("https://tab.com.au/customerId", ""))
        if not customer_id:
            raise Exception("Token missing customerId claim")
        return {
            "token": token,
            "account_number": customer_id,
            "customer_id": customer_id,
            "email": email,
            "proxy_url": proxy_url,
            "claims": claims,
        }
    except MFARequiredException as mfa:
        return {
            "mfa_required": True,
            "mfa_token": mfa.mfa_token,
            "oob_code": mfa.oob_code,
            "email": email,
            "proxy_url": proxy_url,
            "_login_instance": login,
        }
    except Exception:
        login.close()
        raise


async def complete_mfa_login(login_instance: TABLogin, mfa_token: str, oob_code: str, otp_code: str) -> dict:
    """Complete MFA login with the OTP code. Returns same dict as browser_login."""
    try:
        login_instance.complete_mfa(mfa_token, oob_code, otp_code)
        token = login_instance.access_token
        if not token:
            raise Exception("No access token after MFA")
        claims = decode_token_claims(token)
        customer_id = str(claims.get("https://tab.com.au/customerId", ""))
        if not customer_id:
            raise Exception("Token missing customerId claim")
        return {
            "token": token,
            "account_number": customer_id,
            "customer_id": customer_id,
            "email": login_instance.email,
            "proxy_url": login_instance.proxy or "",
            "claims": claims,
        }
    finally:
        login_instance.close()
