"""Scan all pairs in To-Score-10+-Points market, find pair with biggest
correlation-penalty gap between raw multi and SGM-adjusted odds."""
import asyncio, sys
from itertools import combinations
from entain_client import EntainClient

EVENT_ID = "27b7d5e0-55c2-445e-9cdb-bb013d9d9e01"
MARKET = "2d55ebb0-d62e-4d7a-b457-44e83255c96d"


async def main():
    c = EntainClient(brand="neds")
    await c.login(sys.argv[1], sys.argv[2])
    card = await c.get_event_card(EVENT_ID)
    entrants = card["entrants"]
    prices = card["prices"]
    market = card["markets"][MARKET]
    eids = [eid for eid in market["entrant_ids"]
            if entrants.get(eid, {}).get("visible", True)]
    print(f"Scanning {len(eids)} entrants in 'To Score 10+ Points' "
          f"-> {len(eids) * (len(eids) - 1) // 2} pairs")

    # Build single-leg odds map
    def single_dec(entrant_id):
        for k, v in prices.items():
            if k.startswith(entrant_id) and isinstance(v, dict):
                o = v.get("odds", {})
                if o.get("numerator", 0) > 0 and o.get("denominator", 0) > 0:
                    return 1 + o["numerator"] / o["denominator"]
        return None

    singles = {eid: single_dec(eid) for eid in eids}
    for eid, d in singles.items():
        print(f"  {entrants[eid]['name']}: {d:.2f}" if d else f"  {entrants[eid]['name']}: ??")

    results = []
    for a, b in combinations(eids, 2):
        if singles.get(a) is None or singles.get(b) is None: continue
        raw = singles[a] * singles[b]
        try:
            q = await c.get_sgm_quote(EVENT_ID, [
                {"market_id": MARKET, "entrant_id": a},
                {"market_id": MARKET, "entrant_id": b},
            ])
        except Exception as e:
            print(f"  quote failed: {e!r}")
            continue
        p = q.get("prices", {}).get(EVENT_ID, {})
        if not p.get("available"): continue
        o = p["odds"]
        sgm_dec = 1 + o["numerator"] / o["denominator"]
        penalty_pct = (raw - sgm_dec) / raw * 100
        results.append((penalty_pct, sgm_dec, raw, a, b, entrants[a]["name"], entrants[b]["name"], o))

    results.sort(reverse=True)
    print("\n=== TOP 6 by correlation penalty ===")
    for r in results[:6]:
        pct, sgm, raw, a, b, na, nb, odds = r
        print(f"  {pct:+5.1f}%  SGM {sgm:.2f}  raw {raw:.2f}  | {na}  +  {nb}   odds={odds}")

    print("\n=== BOTTOM 3 (most anti-correlated / biggest boost) ===")
    for r in results[-3:]:
        pct, sgm, raw, a, b, na, nb, odds = r
        print(f"  {pct:+5.1f}%  SGM {sgm:.2f}  raw {raw:.2f}  | {na}  +  {nb}")

    await c.close()


asyncio.run(main())
