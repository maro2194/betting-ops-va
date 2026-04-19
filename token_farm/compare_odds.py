"""Show raw individual odds vs SGM correlation-adjusted odds for Mobley + Allen."""
import asyncio, json, sys
from entain_client import EntainClient

EVENT_ID = "27b7d5e0-55c2-445e-9cdb-bb013d9d9e01"
MARKET = "2d55ebb0-d62e-4d7a-b457-44e83255c96d"
MOBLEY = "0a924f36-5759-4c06-a2f3-ab3f171d282c"
ALLEN  = "ea373c5b-1d10-47b9-a772-abe2f7593824"


async def main():
    c = EntainClient(brand="neds")
    await c.login(sys.argv[1], sys.argv[2])

    # Get event card to pull individual entrant prices
    card = await c.get_event_card(EVENT_ID)
    prices = card.get("prices", {})
    market = card.get("markets", {}).get(MARKET, {})
    print(f"Market: {market.get('name')!r}  entrants_in_market={len(market.get('entrant_ids', []))}")

    # Prices keyed by "entrant_id:source:more" in Entain racing. Check.
    print("\nSample price keys (first 6):")
    for i, k in enumerate(list(prices.keys())[:6]):
        print(f"  {k} -> {prices[k]}")

    # Look for keys starting with each entrant id
    def find_price(entrant_id):
        for k, v in prices.items():
            if k.startswith(entrant_id):
                odds = v.get("odds") if isinstance(v, dict) else None
                if odds and odds.get("numerator", 0) > 0 and odds.get("denominator", 0) > 0:
                    return odds, k
        return None, None

    m_odds, mk = find_price(MOBLEY)
    a_odds, ak = find_price(ALLEN)
    print(f"\nMobley 10+ pts odds: {m_odds}  key={mk}")
    if m_odds: print(f"  decimal: {1 + m_odds['numerator']/m_odds['denominator']:.2f}")
    print(f"Allen 10+ pts odds: {a_odds}  key={ak}")
    if a_odds: print(f"  decimal: {1 + a_odds['numerator']/a_odds['denominator']:.2f}")

    # SGM quote both legs
    sgm = await c.get_sgm_quote(EVENT_ID, [
        {"market_id": MARKET, "entrant_id": MOBLEY},
        {"market_id": MARKET, "entrant_id": ALLEN},
    ])
    p = sgm["prices"][EVENT_ID]
    sgm_odds = p["odds"]
    sgm_dec = 1 + sgm_odds["numerator"]/sgm_odds["denominator"]
    print(f"\nSGM odds (both legs): {sgm_odds}  decimal={sgm_dec:.2f}  available={p.get('available')}")

    if m_odds and a_odds:
        m_dec = 1 + m_odds["numerator"]/m_odds["denominator"]
        a_dec = 1 + a_odds["numerator"]/a_odds["denominator"]
        raw_multi = m_dec * a_dec
        print(f"\n=== COMPARISON ===")
        print(f"  Raw multi (if uncorrelated): {m_dec:.2f} x {a_dec:.2f} = {raw_multi:.2f}")
        print(f"  SGM correlation-adjusted:     {sgm_dec:.2f}")
        print(f"  SGM is {((raw_multi - sgm_dec) / raw_multi) * 100:.1f}% lower (correlation penalty)")

    await c.close()


asyncio.run(main())
