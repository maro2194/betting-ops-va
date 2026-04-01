"""
Hardened headers for BetMakers API requests.
Matches exactly what the TerryBet frontend sends from a real Chrome browser.
"""

# Headers the real browser sends to platform.terrybet.bmapollo.com
PLATFORM_HEADERS = {
    "accept": "application/graphql-response+json, application/json",
    "content-type": "application/json",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "referer": "https://terrybet.com.au/",
}

# Racing endpoint uses the same base headers
RACING_HEADERS = {
    "accept": "application/graphql-response+json, application/json",
    "content-type": "application/json",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "referer": "https://terrybet.com.au/",
}


def make_platform_headers(access_token: str, client_id: str) -> dict:
    return {
        **PLATFORM_HEADERS,
        "authorization": f"Bearer {access_token}",
        "x-api-key": client_id,
    }


def make_racing_headers(client_id: str) -> dict:
    return {
        **RACING_HEADERS,
        "x-api-key": client_id,
    }


# --- Per-account proxy guidelines ---
#
# CRITICAL: Each account MUST use its own sticky proxy session.
# Do NOT reuse the same session ID across accounts.
#
# Oxylabs format:
#   http://customer-marolete_86olc-cc-au-sessid-{UNIQUE_PER_ACCOUNT}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777
#
# Generate a unique session ID per account:
#   import random
#   sess_id = str(random.randint(1000000000, 9999999999))
#   proxy = f"http://customer-marolete_86olc-cc-au-sessid-{sess_id}-sesstime-10:K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
#
# The sesstime-10 gives a 10-minute sticky session.
# After 10 min the IP rotates — fine for betting (login + bet takes <30s).
