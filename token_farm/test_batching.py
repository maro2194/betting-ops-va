"""Test: can /v2/same-game-multi/get-odds return MULTIPLE quotes in one call?"""
import asyncio, json, sys, uuid
from urllib.parse import quote
from entain_client import EntainClient

EVENT = "27b7d5e0-55c2-445e-9cdb-bb013d9d9e01"
MARKET_10 = "2d55ebb0-d62e-4d7a-b457-44e83255c96d"
# Four player entrant_ids we know from prior scan
PLAYERS = [
    ("0a924f36-5759-4c06-a2f3-ab3f171d282c", "Mobley"),
    ("ea373c5b-1d10-47b9-a772-abe2f7593824", "Allen"),
    ("0047b561-88be-4c1e-8323-23341043b5d2", "Strus"),
    ("00593e6d-4b0c-40f2-b626-15367c174ad6", "Quickley"),
]


async def main():
    c = EntainClient(brand="neds")
    await c.login(sys.argv[1], sys.argv[2])

    # Build a multi-quote request: 3 different SGMs in one shot
    batch = {}
    labels = []
    combos = [(0, 1), (0, 2), (1, 3)]  # Mobley+Allen, Mobley+Strus, Allen+Quickley
    for a, b in combos:
        k = str(uuid.uuid4())
        batch[k] = {"event_id": EVENT, "selections": [
            {"market_id": MARKET_10, "entrant_id": PLAYERS[a][0]},
            {"market_id": MARKET_10, "entrant_id": PLAYERS[b][0]},
        ]}
        labels.append(f"{PLAYERS[a][1]}+{PLAYERS[b][1]}")

    qs = quote(json.dumps(batch))
    url = f"{c.api_base}/v2/same-game-multi/get-odds?same_game_multies={qs}"
    http = c._client()
    r = await http.get(url)
    print(f"HTTP {r.status_code}  body_len={len(r.text)}")
    data = r.json()
    print(f"Keys returned: {list(data.get('prices', {}).keys())}")
    print(f"Expected keys: {list(batch.keys())}")
    for k, label in zip(batch.keys(), labels):
        p = data.get("prices", {}).get(k, {})
        if p.get("available"):
            o = p["odds"]
            dec = 1 + o["numerator"] / o["denominator"]
            print(f"  {label}: SGM {dec:.2f}  {o}")
        else:
            print(f"  {label}: not available")
    await c.close()


asyncio.run(main())
