"""
Run this LOCALLY (on your Windows machine) to get Sportsbet tokens.
Tokens are saved to sb_tokens.json which you upload to the VPS.
The VPS then uses the refresh_token for API calls without needing a browser.

Usage:
    python3 sb_local_login.py
"""
import json
import os
import sys
import time

# Add sportsbet-cli to path
SB_CLI = r"C:\Users\sml21\sportsbet-cli"
sys.path.insert(0, SB_CLI)

from sportsbet_cli.client import SportsbetClient

EMAIL = "santiago.marino@me.com"
PASSWORD = "SM2194acb"
PROXY = "http://user001:pizza33@193.30.101.8:12323"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "sb_tokens.json")

def main():
    client = SportsbetClient(proxy=PROXY)

    # Try refresh first if we have a saved token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            saved = json.load(f)
        refresh = saved.get("refresh_token")
        if refresh:
            print("Trying saved refresh token...")
            result = client.try_refresh_token(refresh)
            if result:
                print(f"Refresh worked! Token valid until {time.strftime('%H:%M', time.localtime(client.token_exp))}")
                save_tokens(client, refresh)
                return
            print("Refresh failed, doing browser login...")

    # Browser login
    print(f"Logging in as {EMAIL}...")
    result = client.login(EMAIL, PASSWORD)
    print(f"Login result: {result}")

    if client.access_token:
        save_tokens(client, client.refresh_token)
        print(f"\nTokens saved to {TOKEN_FILE}")
        print(f"Upload to VPS: scp {TOKEN_FILE} root@76.13.214.208:/opt/tab-betting-backend/")
    else:
        print("Login failed!")

def save_tokens(client, refresh_token):
    tokens = {
        "access_token": client.access_token,
        "refresh_token": refresh_token,
        "customer_id": getattr(client, 'customer_id', None),
        "token_exp": client.token_exp,
        "email": EMAIL,
        "saved_at": time.time(),
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"Saved: access_token expires {time.strftime('%H:%M', time.localtime(client.token_exp))}")
    print(f"       refresh_token: {refresh_token[:40]}...")

if __name__ == "__main__":
    main()
