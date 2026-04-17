"""Overnight Entain HTTP login test.

Usage:  python3 test_entain_http.py <brand> <username> <password>

Runs the HTTP path (entain_login.py) with Oxylabs AU residential proxy,
then if successful, reads balance via platforms/ladbrokes.py.

Exits 0 on full success (access_token + balance), 1 otherwise.
Output is structured for overnight log parsing.
"""
import asyncio
import logging
import random
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("entain_test")


def build_oxylabs_proxy() -> str:
    sess = random.randint(1000000000, 9999999999)
    return (
        f"http://customer-marolete_86olc-cc-au-sessid-{sess}-sesstime-10:"
        f"K5E=2qcyhfyFZs~@pr.oxylabs.io:7777"
    )


async def main():
    if len(sys.argv) != 4:
        print("Usage: test_entain_http.py <brand> <username> <password>")
        sys.exit(2)

    brand, username, password = sys.argv[1], sys.argv[2], sys.argv[3]
    proxy = build_oxylabs_proxy()

    print(f"=== ENTAIN HTTP TEST ===")
    print(f"brand={brand}  user={username}  proxy={proxy[:60]}...")
    t0 = time.time()

    from entain_login import entain_browser_login
    result = await entain_browser_login(
        username=username, password=password, brand=brand, proxy_url=proxy,
    )

    dt = time.time() - t0
    print(f"\n--- LOGIN RESULT ({dt:.1f}s) ---")
    if result.get("success") and result.get("access_token"):
        tok = result["access_token"]
        print(f"LOGIN OK  token_len={len(tok)}  token_prefix={tok[:40]}...")
        print(f"cookies: {list(result.get('cookies', {}).keys())}")
    else:
        print(f"LOGIN FAIL  error={result.get('error')!r}")
        sys.exit(1)

    # Balance check via platforms/ladbrokes.py
    print("\n--- BALANCE CHECK ---")
    from platforms.ladbrokes import EntainClient, ENTAIN_BRANDS
    client = EntainClient()
    session = {
        "access_token": result["access_token"],
        "proxy_url": proxy,
        "brand_config": {**ENTAIN_BRANDS[brand], "brand": brand},
    }
    t1 = time.time()
    bal = await client.get_balance(session)
    print(f"balance={bal}  ({time.time() - t1:.1f}s)")

    if bal > 0:
        print("\n=== FULL SUCCESS — login + balance both work ===")
        sys.exit(0)
    else:
        print("\n=== PARTIAL — token obtained but balance returned 0 ===")
        print("(may mean token invalid for balance endpoint, or account has $0)")
        sys.exit(3)


if __name__ == "__main__":
    asyncio.run(main())
