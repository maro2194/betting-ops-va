"""
Hardened headers for Amused/BlackStream API requests.
Matches what a real Chrome browser sends to BetDeluxe.
"""

# Base headers matching Chrome 131
BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "origin": "https://www.betdeluxe.com.au",
    "referer": "https://www.betdeluxe.com.au/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "accept-language": "en-AU,en-US;q=0.9,en;q=0.8",
}


def make_get_headers(token: str, org_id: str = "400") -> dict:
    return {
        **BROWSER_HEADERS,
        "Authorization": f"Bearer {token}",
        "X-NextGen-OrgId": org_id,
        "X-NextGen-AppVersion": "7.8.0",
    }


def make_post_headers(token: str, org_id: str = "400", channel_id: str = "502") -> dict:
    return {
        **BROWSER_HEADERS,
        "Authorization": f"Bearer {token}",
        "X-NextGen-OrgId": org_id,
        "X-NextGen-ChannelID": channel_id,
        "X-NextGen-AppVersion": "7.8.0",
    }


# --- Per-account proxy guidelines ---
#
# Same as BetMakers: each account needs its own proxy session.
#
# Oxylabs:
#   import random
#   sess_id = str(random.randint(1000000000, 9999999999))
#   proxy = f"http://customer-marolete_86olc-cc-au-sessid-{sess_id}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
#
# Alternatively use the geo.g-w.info proxy:
#   proxy = "http://dHPtGmhbVJldYrdG:36IacMyImj8x2pV8@geo.g-w.info:10080"


# --- Brand config ---
BRANDS = {
    "surge":     {"org_id": "100", "channel_id": "2",    "auth0_tenant": "surge-production.au.auth0.com"},
    "betnation": {"org_id": "300", "channel_id": "402",  "auth0_tenant": "betnation-prd.au.auth0.com"},
    "betdeluxe": {"org_id": "400", "channel_id": "502",  "auth0_tenant": "betdeluxe-prd.au.auth0.com"},
    "pulsebet":  {"org_id": "500", "channel_id": "602",  "auth0_tenant": "pulsebet-prd.au.auth0.com"},
    "bigbet":    {"org_id": "600", "channel_id": "702",  "auth0_tenant": "bigbet-prd.au.auth0.com"},
    "yesbet":    {"org_id": "700", "channel_id": "802",  "auth0_tenant": "yesbet-prd.au.auth0.com"},
    "mightybet": {"org_id": "900", "channel_id": "1002", "auth0_tenant": "mightybet-prd.au.auth0.com"},
}
